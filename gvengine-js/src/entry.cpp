// SPDX-License-Identifier: MPL-2.0
// Copyright (C) 2026 Mark Higgins
// Modifications Copyright (C) 2026 Nicholas Vlamis
//
// This Source Code Form is subject to the terms of the Mozilla Public License,
// v. 2.0. If a copy of the MPL was not distributed with this file, You can
// obtain one at https://mozilla.org/MPL/2.0/.
//
// The browser build's C boundary. Derived from bgsage's `cpp/src/mobile.cpp`
// (the portable C ABI bgsage ships for native clients), and kept MPL-2.0 for
// that reason: it is a modified copy of an MPL file, not code of ours that
// links to one. What changed, and why:
//
// - **Depth.** mobile.cpp stops at 2-ply and a 1s budget, which is what a phone
//   app wants. A browser tab runs this in a worker, so 3- and 4-ply are allowed
//   and the budget goes to ten minutes; the caller decides what is interactive.
// - **Threads.** Each request says how many threads to use. The full checker
//   search hands one candidate to each, which is how BgBotAnalyzer's cubeful
//   wrapper parallelizes the same work.
// - **Two more operations**: a trimmed checker search and a 2T cube rollout
//   (see `Op`).
//
// The full checker search and the n-ply cube are mobile.cpp's code paths
// unchanged, and so reproduce BgBotAnalyzer's numbers -- `test/parity.test.mjs`
// holds them to that.
#include "bgbot/bearoff.h"
#include "bgbot/board.h"
#include "bgbot/cube.h"
#include "bgbot/match_equity.h"
#include "bgbot/moves.h"
#include "bgbot/multipy.h"
#include "bgbot/neural_net.h"
#include "bgbot/pubeval.h"
#include "bgbot/rollout.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <iomanip>
#include <locale>
#include <memory>
#include <sstream>
#include <stdexcept>

using namespace bgbot;

extern "C" {
struct GvEngine {
    BearoffDB db;
    std::shared_ptr<Strategy> base;
    std::shared_ptr<Strategy> strategy;
};

// The request, laid out as 65 consecutive int32s; `engine.js` writes it field
// by field, so the order here is the wire format.
typedef struct {
    int board[26];
    int die1, die2, ply;
    int cube_value, cube_owner; // 0 centered, 1 player, 2 opponent
    int away1, away2, is_crawford, jacoby, beaver;
    int budget_ms; // checked between candidates
    int threads;   // 1 = run on the calling thread only
    int has_played;   // trimmed checker search: `played` is set
    int played[26];   // the position after the play actually made
} GvRequest;
}

namespace {
enum Op {
    CHECKER = 0,          // every candidate at full depth, cube-aware (BgBotAnalyzer's n-ply)
    CUBE = 1,             // n-ply cube decision
    CHECKER_TRIMMED = 2,  // 1-ply screen, a 2-ply cut, then at most five at full depth
    CUBE_2T = 3,          // BgBotAnalyzer's "truncated2" cube rollout
    CUBE_3T = 4,          // and "truncated3"
    CHECKER_2T = 5,       // BgBotAnalyzer's "truncated2" checker play: the whole decision
    CHECKER_3T = 6,       // and "truncated3"
    CHECKER_MATCH = 7,    // gvanalysis.checker_eval's screen, for match analysis
    POST_MOVE = 8,        // BgBotAnalyzer.post_move_analytics of `played`, at n-ply
};

thread_local std::string last_error;
struct Move { Board board; std::array<double, 5> probs; double equity, cubeless; int depth; };
std::array<double, 5> widen(const std::array<float, 5>& p) {
    return {p[0], p[1], p[2], p[3], p[4]};
}
std::array<double, 5> invert(const std::array<float, 5>& p) {
    return {1.0-p[0], p[3], p[4], p[1], p[2]};
}
double cl(const std::array<double,5>& p) { return 2*p[0]-1+p[1]-p[3]+p[2]-p[4]; }
template<typename T, size_t N> void array(std::ostringstream& s, const std::array<T,N>& a) {
    s << '[';
    for(size_t i=0;i<N;++i) { if(i) s << ','; s << a[i]; }
    s << ']';
}
void validate(const GvRequest& r, int op) {
    const bool checker=(op==CHECKER || op==CHECKER_TRIMMED || op==CHECKER_2T || op==CHECKER_3T
                        || op==CHECKER_MATCH || op<0);
    if(op<-1 || op>POST_MOVE || r.ply<1 || r.ply>4 || r.cube_owner<0 || r.cube_owner>2 ||
       r.cube_value<1 || r.cube_value>32768 || (r.cube_value & (r.cube_value-1)) ||
       r.away1<0 || r.away2<0 || r.away1>25 || r.away2>25 ||
       ((r.away1==0)!=(r.away2==0)) || r.budget_ms<1 || r.budget_ms>600000 ||
       r.threads<1 || r.threads>64 ||
       (checker && (r.die1<1 || r.die1>6 || r.die2<1 || r.die2>6)))
        throw std::invalid_argument("unsupported request");
    int player=r.board[25], opponent=r.board[0];
    if(player<0 || opponent<0 || player>15 || opponent>15) throw std::invalid_argument("invalid bar");
    for(int i=1;i<25;++i) {
        if(r.board[i]<-15 || r.board[i]>15) throw std::invalid_argument("invalid checker count");
        if(r.board[i]>0) player+=r.board[i]; else opponent-=r.board[i];
    }
    if(player<1 || player>15 || opponent<1 || opponent>15)
        throw std::invalid_argument("invalid or terminal board");
}

// One candidate at 1-ply, cube-aware by Janowski: the screen.
void score_1ply(const GvEngine& e, const Board& board, const CubeInfo& ci, Move& m) {
    auto p=e.strategy->evaluate_probs(m.board,board); m.probs=widen(p);
    auto [pp,op]=pip_counts(m.board); const auto x=cube_efficiency(p,is_race(m.board),pp,op);
    m.equity=cl2cf(p,ci,x); m.cubeless=cl(m.probs); m.depth=1;
}

// One candidate at `ply`. Mirrors BgBotAnalyzer's cube-aware rescore,
// including root routing and the flipped match state.
void score_deep(const GvEngine& e, const Board& board, const CubeInfo& ci, int ply, Move& m) {
    const MoveFilter filter{5,0.08f};
    auto v=cubeful_probs_and_equity_nply(flip(m.board),flip_cube_perspective(ci),
        *e.strategy,ply,filter,1,nullptr,&board);
    m.probs=invert(v.probs); m.equity=-v.equity; m.cubeless=cl(m.probs); m.depth=ply;
}

// multipy_parallel_for, except that one thread never touches the pool: the
// pool's first use starts its threads, which a build without pthreads cannot.
void parallel(int n, int threads, const std::function<void(int)>& fn) {
    if(threads<=1) { for(int i=0;i<n;++i) fn(i); return; }
    multipy_parallel_for(n,threads,fn);
}

bool better(const Move& a, const Move& b) {
    if(a.equity!=b.equity) return a.equity>b.equity;
    if(a.cubeless!=b.cubeless) return a.cubeless>b.cubeless;
    return a.board<b.board;
}

void write_moves(std::ostringstream& s, const std::vector<Move>& moves) {
    s << "{\"moves\":[";
    for(size_t i=0;i<moves.size();++i) { const auto& m=moves[i]; if(i) s << ',';
        s << "{\"board\":"; array(s,m.board); s << ",\"probs\":"; array(s,m.probs);
        s << ",\"equity\":" << m.equity << ",\"cubeless_equity\":" << m.cubeless
          << ",\"equity_diff\":" << m.equity-moves[0].equity
          << ",\"eval_level\":\"" << m.depth << "-ply\"}";
    } s << "]}";
}

// The trimmed search's two cuts, as (at most, within) of the best value at the
// stage before. Chosen on 911 decisions from real matches, scored against the
// full search: with the played move searched too, the best play it misses is a
// tie (< 0.0005 equity), and no graded play moves by 0.01 -- at about a
// quarter of the time, and a tenth of the worst case. The 2-ply stage is
// what earns that: a one-stage cut straight from 1-ply to five plays missed
// three times as often, by up to 0.048.
constexpr int SCREEN_KEEP=16;   constexpr double SCREEN_WINDOW=0.20;   // 1-ply -> 2-ply
constexpr int SEARCH_KEEP=5;    constexpr double SEARCH_WINDOW=0.08;   // 2-ply -> full depth

// Keep the best `keep` of `alive` within `window` of the best of them, by
// their current values, and search those at `ply`.
void cut(const GvEngine& e, const GvRequest& r, const Board& board, const CubeInfo& ci,
         std::vector<Move>& moves, std::vector<int>& alive, int keep, double window, int ply) {
    std::sort(alive.begin(),alive.end(),[&](int a,int b) { return better(moves[a],moves[b]); });
    const double best=moves[alive[0]].equity;
    std::vector<int> kept;
    for(int i:alive) { if((int)kept.size()==keep) break; if(best-moves[i].equity<window) kept.push_back(i); }
    alive=kept;
    parallel((int)alive.size(), r.threads, [&](int k) { score_deep(e,board,ci,ply,moves[alive[k]]); });
}

void checker(const GvEngine& e, const GvRequest& r, const Board& board, const CubeInfo& ci,
             bool trimmed, const std::function<void()>& check_budget, std::ostringstream& s) {
    std::vector<Board> candidates; possible_boards(board,r.die1,r.die2,candidates);
    std::vector<Move> moves(candidates.size());
    for(size_t i=0;i<candidates.size();++i) moves[i].board=candidates[i];
    if(r.ply==1) {
        for(auto& m:moves) { check_budget(); score_1ply(e,board,ci,m); }
    } else if(!trimmed) {
        parallel((int)moves.size(), r.threads, [&](int i) { score_deep(e,board,ci,r.ply,moves[i]); });
        check_budget();
    } else if(!moves.empty()) {
        for(auto& m:moves) score_1ply(e,board,ci,m);
        std::vector<int> alive(moves.size());
        for(size_t i=0;i<moves.size();++i) alive[i]=(int)i;
        cut(e,r,board,ci,moves,alive,SCREEN_KEEP,SCREEN_WINDOW,2);
        check_budget();
        if(r.ply>2) { cut(e,r,board,ci,moves,alive,SEARCH_KEEP,SEARCH_WINDOW,r.ply); check_budget(); }
        // The play being graded is searched whatever the cuts thought of it,
        // so its error is measured between two full-depth numbers -- as
        // BgBotAnalyzer's `force_boards` does for the same reason.
        if(r.has_played) {
            Board played{}; std::copy(r.played,r.played+26,played.begin());
            auto it=std::find_if(moves.begin(),moves.end(),[&](const Move& m) { return m.board==played; });
            if(it==moves.end()) throw std::invalid_argument("played board is not a legal play");
            if(it->depth<r.ply) score_deep(e,board,ci,r.ply,*it);
        }
        // The rule BgBotAnalyzer keeps (`_first_stale_top_two`): no shallow
        // value may sit in the top two, because whenever the deep search
        // values every survivor below a pruned move's shallower number, that
        // move would otherwise top the list on a value nobody checked.
        for(;;) {
            check_budget();
            std::sort(moves.begin(),moves.end(),better);
            const auto top=moves.begin()+std::min<size_t>(2,moves.size());
            auto stale=std::find_if(moves.begin(),top,[&](const Move& m) { return m.depth<r.ply; });
            if(stale==top) break;
            score_deep(e,board,ci,r.ply,*stale);
        }
    }
    std::sort(moves.begin(),moves.end(),better);
    write_moves(s,moves);
}

void cube_nply(const GvEngine& e, const GvRequest& r, const Board& board, const CubeInfo& ci,
               const std::function<void()>& check_budget, std::ostringstream& s) {
    const MoveFilter filter{5,0.08f};
    std::array<float,5> p; CubeDecision cd;
    if(r.ply==1) {
        p=invert_probs(e.strategy->evaluate_probs(flip(board),is_race(board)));
        auto [pp,op]=pip_counts(board);
        cd=cube_decision_1ply(p,ci,cube_efficiency(p,is_race(board),pp,op));
    } else {
        PubEval prefilter;
        cd=cube_decision_nply(board,ci,*e.strategy,r.ply,filter,r.threads,&prefilter);
        check_budget();
        MultiPlyStrategy multipy(e.strategy,r.ply,filter,false,r.threads>1,r.threads);
        multipy.set_bearoff_db(&e.db);
        const Board flipped=flip(board);
        p=invert_probs(multipy.evaluate_probs(flipped,flipped)); multipy.clear_cache();
    }
    const char* action=!cd.should_double ? "No Double" : cd.is_beaver ? "Double/Beaver" : cd.should_take ? "Double/Take" : "Double/Pass";
    s << "{\"probs\":"; array(s,p);
    s << ",\"cubeless_equity\":" << cubeless_equity(p)
      << ",\"equity_nd\":" << cd.equity_nd << ",\"equity_dt\":" << cd.equity_dt
      << ",\"equity_dp\":" << cd.equity_dp << ",\"should_double\":" << cd.should_double
      << ",\"should_take\":" << cd.should_take << ",\"optimal_equity\":" << cd.optimal_equity
      << ",\"optimal_action\":\"" << action << "\",\"is_beaver\":" << cd.is_beaver
      << ",\"eval_level\":\"" << r.ply << "-ply\",\"cubeless_se\":null,\"equity_nd_se\":null,\"equity_dt_se\":null}";
}

// BgBotAnalyzer's "truncated2" and "truncated3" rollouts (analyzer.py, the
// eval_level branches): 360 trials truncated at 7, with seed 42 -- whose dice
// are stratified from the seed and cached on the strategy, so every board
// rolled out under one config sees the same 360 sequences, as on the server.
RolloutConfig rollout_config(int level, int threads) {
    RolloutConfig rc;
    rc.n_trials=360; rc.truncation_depth=7; rc.n_threads=threads; rc.seed=42;
    rc.parallelize_trials=true; rc.ultra_late_threshold=9999;
    rc.cubeful_trial_moves=true; rc.cubeful_late_threshold=0;
    if(level==2) {
        rc.decision_ply=2; rc.truncation_ply=2; rc.late_ply=1; rc.late_threshold=1;
        rc.cube.ply=2; rc.cube_late.ply=2;
    } else {
        rc.decision_ply=3; rc.late_ply=2; rc.late_threshold=2;
    }
    return rc;
}

// A whole checker decision at 2T or 3T, as BgBotAnalyzer reads one: its
// rollout analyzer's `checker_play_analytics` (stage 1, every play at 1-ply,
// kept within 0.15; stage 2, those at 2-ply, kept to five within 0.08 and at
// least two; the survivors rolled out), then its cubeful wrapper's rollout
// path (every other play given a cubeful value at the rollout's decision ply,
// and whichever play tops the list rolled out until a rolled-out one does).
// Order-sensitive steps keep Python's: the filters' sorts are stable on the
// cubeful value alone, and the final order is `_move_order_key`'s.
struct Scored { Board board; std::array<double,5> probs; double cl, cf; };

std::vector<Scored> score_with(const Strategy& strategy, const std::vector<Board>& boards,
                               const Board& board, const CubeInfo& ci) {
    std::vector<Scored> out;
    for(const auto& b:boards) {
        const auto p=strategy.evaluate_probs(b,board);
        auto [pp,op]=pip_counts(b); const auto x=cube_efficiency(p,is_race(b),pp,op);
        out.push_back({b,widen(p),NeuralNetwork::compute_equity(p),cl2cf(p,ci,x)});
    }
    std::stable_sort(out.begin(),out.end(),[](const Scored& a,const Scored& b) { return a.cf>b.cf; });
    return out;
}

std::vector<Scored> keep(const std::vector<Scored>& scored, double window, size_t most) {
    std::vector<Scored> out;
    for(const auto& s:scored) {
        if(out.size()==most) break;
        if(scored[0].cf-s.cf<window) out.push_back(s);
    }
    return out;
}

struct Row { Board board; std::array<double,5> probs; double cl, cf, se; const char* level; bool rolled; };

void checker_rollout(const GvEngine& e, const GvRequest& r, const Board& board, const CubeInfo& ci,
                     int level, const std::function<void()>& check_budget, std::ostringstream& s) {
    std::vector<Board> candidates; possible_boards(board,r.die1,r.die2,candidates);
    std::vector<Row> rows;
    if(!candidates.empty()) {
        const auto has=[](const std::vector<Scored>& v,const Board& b) {
            return std::any_of(v.begin(),v.end(),[&](const Scored& x) { return x.board==b; }); };
        const auto s1=score_with(*e.strategy,candidates,board,ci);
        const auto stage1=keep(s1,0.15,s1.size());
        MultiPlyStrategy two(e.base,2,MoveFilter{5,0.08f},false,r.threads>1,r.threads);
        two.set_move_prefilter(std::make_shared<PubEval>());
        two.set_bearoff_db(&e.db);
        std::vector<Scored> s2, survivors;
        const bool staged=stage1.size()>1;
        if(staged) {
            std::vector<Board> b1; for(const auto& x:stage1) b1.push_back(x.board);
            s2=score_with(two,b1,board,ci);
            survivors=keep(s2,0.08,5);
            two.clear_cache();
        } else {
            survivors=keep(stage1,0.08,5);
        }
        check_budget();
        const auto& pool=staged ? s2 : s1;
        for(size_t i=0;survivors.size()<2 && i<pool.size();++i)
            if(!has(survivors,pool[i].board)) survivors.push_back(pool[i]);
        if(r.has_played) {
            Board played{}; std::copy(r.played,r.played+26,played.begin());
            if(std::find(candidates.begin(),candidates.end(),played)==candidates.end())
                throw std::invalid_argument("played board is not a legal play");
            if(!has(survivors,played)) {
                auto it=std::find_if(pool.begin(),pool.end(),[&](const Scored& x) { return x.board==played; });
                if(it!=pool.end()) survivors.push_back(*it);
                else survivors.push_back(score_with(staged ? static_cast<const Strategy&>(two) : *e.strategy,
                                                    {played},board,ci)[0]);
            }
        }

        RolloutStrategy rollout(e.base,rollout_config(level,r.threads)); rollout.set_bearoff_db(&e.db);
        const auto roll=[&](Row& row) {
            check_budget();
            const auto cpr=rollout.cubeful_rollout_position(row.board,ci);
            row.probs=widen(e.db.is_bearoff(row.board) ? e.db.lookup_probs(row.board,true) : cpr.cubeless.mean_probs);
            row.cl=cpr.cubeless.equity; row.cf=cpr.cubeful_equity; row.se=cpr.cubeful_se;
            row.level="Rollout"; row.rolled=true;
        };
        for(const auto& x:survivors) { rows.push_back({x.board,x.probs,x.cl,0,0,"",false}); roll(rows.back()); }
        for(const auto& x:s2) if(!has(survivors,x.board)) rows.push_back({x.board,x.probs,x.cl,0,0,"2-ply",false});
        for(const auto& x:s1) if(!has(survivors,x.board) && !has(s2,x.board))
            rows.push_back({x.board,x.probs,x.cl,0,0,"1-ply",false});

        // The cubeful wrapper's value for a play that was not rolled out:
        // cubeful_equity_nply at the rollout's decision ply, from the side
        // about to roll (`_cubeful_equity`).
        const int ply=rollout_config(level,1).decision_ply;
        std::vector<int> rest;
        for(size_t i=0;i<rows.size();++i) if(!rows[i].rolled) rest.push_back((int)i);
        parallel((int)rest.size(), r.threads, [&](int k) {
            auto& row=rows[rest[k]];
            row.cf=-cubeful_equity_nply(flip(row.board),flip_cube_perspective(ci),*e.strategy,ply,
                                        MoveFilter{5,0.08f},1);
        });
        const auto order=[](const Row& a,const Row& b) {
            if(a.cf!=b.cf) return a.cf>b.cf;
            if(a.cl!=b.cl) return a.cl>b.cl;
            return a.board<b.board;
        };
        std::sort(rows.begin(),rows.end(),order);
        while(!rows.empty() && !rows[0].rolled) { roll(rows[0]); std::sort(rows.begin(),rows.end(),order); }
    }
    s << "{\"moves\":[";
    for(size_t i=0;i<rows.size();++i) { const auto& m=rows[i]; if(i) s << ',';
        s << "{\"board\":"; array(s,m.board); s << ",\"probs\":"; array(s,m.probs);
        s << ",\"equity\":" << m.cf << ",\"cubeless_equity\":" << m.cl
          << ",\"equity_diff\":" << m.cf-rows[0].cf << ",\"eval_level\":\"" << m.level << "\"";
        if(m.rolled) s << ",\"se\":" << m.se;
        s << "}";
    } s << "]}";
}

// A checker play as gammonview-analysis's match analysis reads one
// (`gvanalysis.checker_eval.checker_play`, staged, unseeded): every play at
// 1-ply; past 2-ply, the top STAGE_SCREEN_MOVES (or every play within
// STAGE_SCREEN_WINDOW of the 1-ply leader, at most STAGE_SCREEN_CAP) at 2-ply,
// then those within STAGE_THRESHOLD of the 2-ply leader (at most
// STAGE_MAX_MOVES) at full depth; at 2-ply, the flat screen -- within
// ELEVATE_THRESHOLD (at most ELEVATE_MAX_MOVES, never fewer than
// ELEVATE_MIN_MOVES) at full depth. The played move joins every stage. The
// list is groups, deepest first, each sorted (stably) on its own equity, as
// that module returns it; a one-ply request is the full search.
void checker_match(const GvEngine& e, const GvRequest& r, const Board& board, const CubeInfo& ci,
                   const std::function<void()>& check_budget, std::ostringstream& s) {
    std::vector<Board> candidates; possible_boards(board,r.die1,r.die2,candidates);
    std::vector<Move> out;
    if(!candidates.empty() && r.ply==1) {
        for(const auto& b:candidates) { Move m{}; m.board=b; score_1ply(e,board,ci,m); out.push_back(m); }
        std::stable_sort(out.begin(),out.end(),[](const Move& a,const Move& b) { return a.equity>b.equity; });
    } else if(!candidates.empty()) {
        std::vector<Move> scored;
        for(const auto& b:candidates) { Move m{}; m.board=b; score_1ply(e,board,ci,m); scored.push_back(m); }
        std::stable_sort(scored.begin(),scored.end(),[](const Move& a,const Move& b) { return a.equity>b.equity; });
        Board played{}; bool has_played=r.has_played!=0;
        if(has_played) std::copy(r.played,r.played+26,played.begin());
        // `_with_forced`: the played move's index, when not already chosen.
        const auto with_played=[&](std::vector<int> chosen) {
            if(!has_played) return chosen;
            for(int i:chosen) if(scored[i].board==played) return chosen;
            for(size_t i=0;i<scored.size();++i) if(scored[i].board==played) { chosen.push_back((int)i); break; }
            return chosen;
        };
        const auto group=[&](std::vector<Move> ms,int ply) {
            parallel((int)ms.size(), r.threads, [&](int k) { score_deep(e,board,ci,ply,ms[k]); });
            std::stable_sort(ms.begin(),ms.end(),[](const Move& a,const Move& b) { return a.equity>b.equity; });
            return ms;
        };
        std::vector<int> chosen;
        std::vector<Move> deep, mid;
        if(r.ply>2) {
            const double lead1=scored[0].equity; int near=0;
            for(const auto& m:scored) if(lead1-m.equity<0.2) ++near;
            const size_t n2=std::max<size_t>(14,std::min(near,40));
            for(size_t i=0;i<std::min(n2,scored.size());++i) chosen.push_back((int)i);
            chosen=with_played(chosen);
            std::vector<Move> b2; for(int i:chosen) b2.push_back(scored[i]);
            mid=group(b2,2);
            check_budget();
            const double lead=mid[0].equity;
            std::vector<Move> keep;
            for(const auto& m:mid) { if(keep.size()==12) break; if(lead-m.equity<0.16) keep.push_back(m); }
            if(has_played && std::none_of(keep.begin(),keep.end(),[&](const Move& m) { return m.board==played; }))
                for(const auto& m:mid) if(m.board==played) { keep.push_back(m); break; }
            deep=group(keep,r.ply);
            std::vector<Move> rest;
            for(const auto& m:mid)
                if(std::none_of(keep.begin(),keep.end(),[&](const Move& k) { return k.board==m.board; })) rest.push_back(m);
            mid=rest;
        } else {
            const double lead1=scored[0].equity; size_t near=0;
            for(const auto& m:scored) { if(near==12) break; if(lead1-m.equity<0.16) ++near; }
            for(size_t i=0;i<std::min(std::max<size_t>(near,14),scored.size());++i) chosen.push_back((int)i);
            chosen=with_played(chosen);
            std::vector<Move> b; for(int i:chosen) b.push_back(scored[i]);
            deep=group(b,r.ply);
        }
        out=deep; out.insert(out.end(),mid.begin(),mid.end());
        for(size_t i=0;i<scored.size();++i)
            if(std::find(chosen.begin(),chosen.end(),(int)i)==chosen.end()) out.push_back(scored[i]);
    }
    write_moves(s,out);
}

// One post-move board, as BgBotAnalyzer.post_move_analytics reads it at an
// n-ply level: cubeless probabilities from the cube-aware tree on the side about
// to roll (`cubeful_probs_nply`, the cube and the scores turned round, beavers
// left at the binding's default), then the cubeful equity by Janowski on those
// probabilities from the mover's side. Match analysis asks this of a played
// board the move list does not hold -- an illegal or restated play.
void post_move(const GvEngine& e, const GvRequest& r, const CubeInfo& ci, std::ostringstream& s) {
    if(!r.has_played) throw std::invalid_argument("no board to evaluate");
    Board b{}; std::copy(r.played,r.played+26,b.begin());
    std::array<double,5> probs; double cf;
    if(r.ply==1) {
        Move m{}; m.board=b; score_1ply(e,b,ci,m); probs=m.probs; cf=m.equity;
    } else {
        CubeInfo opp=flip_cube_perspective(ci); opp.beaver=ci.is_money();
        const auto op=cubeful_probs_nply(flip(b),opp,*e.strategy,r.ply,MoveFilter{5,0.08f},r.threads);
        probs=invert(op);
        std::array<float,5> p; for(int i=0;i<5;++i) p[i]=static_cast<float>(probs[i]);
        auto [pp,opc]=pip_counts(b); const auto x=cube_efficiency(p,is_race(b),pp,opc);
        cf=cl2cf(p,ci,x);
    }
    s << "{\"board\":"; array(s,b); s << ",\"probs\":"; array(s,probs);
    s << ",\"cubeless_equity\":" << cl(probs) << ",\"equity\":" << cf
      << ",\"eval_level\":\"" << r.ply << "-ply\"}";
}

// A cube decision at 2T or 3T: the rollout above, and the ND/DT/DP reading of
// it that bgsage's Python binding does after `cubeful_cube_decision`
// (`bindings.cpp`, RolloutStrategy.cube_decision). The beaver flag of the
// result is not reproduced; nothing here reads it yet.
void cube_rollout(const GvEngine& e, const GvRequest& r, const Board& board, const CubeInfo& ci,
                  int level, std::ostringstream& s) {
    RolloutStrategy rollout(e.base,rollout_config(level,r.threads)); rollout.set_bearoff_db(&e.db);
    const auto cfr=rollout.cubeful_cube_decision(board,ci);
    const bool can=can_double(ci);
    float nd,dt,dp; bool should_double,should_take;
    if(!ci.is_money()) {
        const int a1=ci.match.away1,a2=ci.match.away2,cv=ci.cube_value; const bool cr=ci.match.is_crawford;
        const float nd_m=cfr.nd_equity, dt_m=cfr.dt_equity, dp_m=dp_mwc(a1,a2,cv,cr);
        if(!can) { should_double=false; should_take=true; }
        else {
            should_take=(dt_m<=dp_m);
            should_double=(!cr && a1>1 && a2==1) ? true : (std::min(dt_m,dp_m)>nd_m);
        }
        nd=mwc2eq(nd_m,a1,a2,cv,cr); dt=mwc2eq(dt_m,a1,a2,cv,cr); dp=mwc2eq(dp_m,a1,a2,cv,cr);
    } else {
        dp=1.0f; nd=cfr.nd_equity;
        const float actual_dt=cfr.dt_equity;
        dt=(ci.beaver && actual_dt<0.0f) ? 2.0f*actual_dt : actual_dt;
        if(!can) { should_double=false; should_take=true; }
        else { should_double=std::min(dt,dp)>nd; should_take=dt<=dp; }
    }
    const float optimal=should_double ? std::min(dt,dp) : nd;
    const char* action=!should_double ? "No Double" : should_take ? "Double/Take" : "Double/Pass";
    s << "{\"probs\":"; array(s,cfr.cubeless.mean_probs);
    s << ",\"cubeless_equity\":" << cfr.cubeless.equity
      << ",\"equity_nd\":" << nd << ",\"equity_dt\":" << dt << ",\"equity_dp\":" << dp
      << ",\"should_double\":" << should_double << ",\"should_take\":" << should_take
      << ",\"optimal_equity\":" << optimal << ",\"optimal_action\":\"" << action
      << "\",\"eval_level\":\"Rollout\",\"cubeless_se\":" << cfr.cubeless.std_error
      << ",\"equity_nd_se\":" << cfr.nd_se << ",\"equity_dt_se\":" << cfr.dt_se << "}";
}
} // namespace

extern "C" {
GvEngine* gvengine_create(const char* type, const char* const* paths, const int* hidden,
                          int count, const char* bearoff) {
    try {
        if(!type || !paths || !hidden || !bearoff || count<5 || count>32)
            throw std::invalid_argument("invalid model configuration");
        auto e=std::make_unique<GvEngine>();
        std::vector<std::string> p; std::vector<int> h;
        for(int i=0;i<count;++i) {
            if(!paths[i] || hidden[i]<1 || hidden[i]>4096) throw std::invalid_argument("invalid model asset");
            p.emplace_back(paths[i]); h.push_back(hidden[i]);
        }
        const std::string t(type);
        if(t=="backgame_pair_phased") e->base=std::make_shared<BackgameAwarePairStrategy>(p,h,true);
        else if(t=="backgame_pair") e->base=std::make_shared<BackgameAwarePairStrategy>(p,h);
        else if(t=="pair") e->base=std::make_shared<GamePlanPairStrategy>(p,h);
        else if(t=="5nn") e->base=std::make_shared<GamePlanStrategy>(p,h);
        else throw std::invalid_argument("unsupported model strategy");
        if(!e->db.load(bearoff)) throw std::runtime_error("bearoff asset unavailable");
        e->strategy=std::make_shared<BearoffStrategy>(e->base,&e->db);
        last_error.clear(); return e.release();
    } catch(const std::exception& ex) { last_error=ex.what(); return nullptr; }
    catch(...) { last_error="engine initialization failed"; return nullptr; }
}
void gvengine_destroy(GvEngine* e) { delete e; }
void gvengine_free(char* p) { std::free(p); }
const char* gvengine_last_error() { return last_error.c_str(); }

// Named plays of a checker decision, each rolled out at 2T or 3T: what the
// server's `POST /position/moves` answers (gvanalysis.positionmove, through
// BgBotAnalyzer.post_move_analytics, to RolloutStrategy::
// cubeful_rollout_position). `boards` is `count` post-move boards of 26 ints,
// each one a legal play of the request's dice. Probabilities are the
// rollout's own, as the server's are for an upgraded row.
char* gvengine_rollout_moves(GvEngine* e, int level, const GvRequest* request,
                             const int* boards, int count) {
    try {
        if(!e || !request || !boards) throw std::invalid_argument("engine unavailable");
        if(level!=2 && level!=3) throw std::invalid_argument("unsupported rollout level");
        if(count<1 || count>8) throw std::invalid_argument("between 1 and 8 moves");
        const auto& r=*request; validate(r,-1);
        Board board{}; std::copy(r.board,r.board+26,board.begin());
        CubeInfo ci{r.cube_value,static_cast<CubeOwner>(r.cube_owner),
            {r.away1,r.away2,r.is_crawford!=0},-1.0f,
            r.away1==0 && r.jacoby!=0,r.away1==0 && r.beaver!=0};
        std::vector<Board> legal; possible_boards(board,r.die1,r.die2,legal);
        std::vector<Board> wanted(count);
        for(int i=0;i<count;++i) {
            std::copy(boards+26*i,boards+26*(i+1),wanted[i].begin());
            if(std::find(legal.begin(),legal.end(),wanted[i])==legal.end())
                throw std::invalid_argument("not a legal play");
        }
        RolloutStrategy rollout(e->base,rollout_config(level,r.threads)); rollout.set_bearoff_db(&e->db);
        std::ostringstream s; s.imbue(std::locale::classic()); s << std::setprecision(17);
        s << "{\"moves\":[";
        for(int i=0;i<count;++i) {
            const auto cpr=rollout.cubeful_rollout_position(wanted[i],ci);
            if(i) s << ',';
            s << "{\"board\":"; array(s,wanted[i]); s << ",\"probs\":"; array(s,cpr.cubeless.mean_probs);
            s << ",\"cubeless_equity\":" << cpr.cubeless.equity << ",\"equity\":" << cpr.cubeful_equity
              << ",\"se\":" << cpr.cubeful_se << ",\"eval_level\":\"" << level << "T\"}";
        }
        s << "]}";
        auto text=s.str(); char* out=static_cast<char*>(std::malloc(text.size()+1));
        if(!out) throw std::bad_alloc(); std::memcpy(out,text.c_str(),text.size()+1);
        last_error.clear(); return out;
    } catch(const std::exception& ex) { last_error=ex.what(); return nullptr; }
    catch(...) { last_error="evaluation failed"; return nullptr; }
}

// JSON text the caller frees with gvengine_free, or null with the reason in
// gvengine_last_error. Never a partial result.
char* gvengine_analyze(GvEngine* e, int op, const GvRequest* request) {
    try {
        if(!e || !request) throw std::invalid_argument("engine unavailable");
        const auto& r=*request; validate(r,op);
        Board board{}; std::copy(r.board,r.board+26,board.begin());
        CubeInfo ci{r.cube_value,static_cast<CubeOwner>(r.cube_owner),
            {r.away1,r.away2,r.is_crawford!=0},-1.0f,
            r.away1==0 && r.jacoby!=0,r.away1==0 && r.beaver!=0};
        const auto deadline=std::chrono::steady_clock::now()+std::chrono::milliseconds(r.budget_ms);
        const std::function<void()> check_budget=[&] {
            if(std::chrono::steady_clock::now()>deadline) throw std::runtime_error("budget exceeded");
        };
        std::ostringstream s; s.imbue(std::locale::classic()); s << std::setprecision(17) << std::boolalpha;
        switch(op) {
            case CHECKER: checker(*e,r,board,ci,false,check_budget,s); break;
            case CHECKER_TRIMMED: checker(*e,r,board,ci,true,check_budget,s); break;
            case CUBE: cube_nply(*e,r,board,ci,check_budget,s); break;
            case CUBE_2T: cube_rollout(*e,r,board,ci,2,s); break;
            case CUBE_3T: cube_rollout(*e,r,board,ci,3,s); break;
            case CHECKER_2T: checker_rollout(*e,r,board,ci,2,check_budget,s); break;
            case CHECKER_3T: checker_rollout(*e,r,board,ci,3,check_budget,s); break;
            case CHECKER_MATCH: checker_match(*e,r,board,ci,check_budget,s); break;
            case POST_MOVE: post_move(*e,r,ci,s); break;
        }
        check_budget(); auto text=s.str(); char* out=static_cast<char*>(std::malloc(text.size()+1));
        if(!out) throw std::bad_alloc(); std::memcpy(out,text.c_str(),text.size()+1);
        last_error.clear(); return out;
    } catch(const std::exception& ex) { last_error=ex.what(); return nullptr; }
    catch(...) { last_error="evaluation failed"; return nullptr; }
}
}
