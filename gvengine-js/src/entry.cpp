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
    const bool checker=(op==CHECKER || op==CHECKER_TRIMMED);
    if(op<CHECKER || op>CUBE_2T || r.ply<1 || r.ply>4 || r.cube_owner<0 || r.cube_owner>2 ||
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

// BgBotAnalyzer's "truncated2" for a cube decision: its RolloutConfig, and the
// ND/DT/DP reading of the result that bgsage's Python binding does after
// `cubeful_cube_decision` (`bindings.cpp`, RolloutStrategy.cube_decision).
// The beaver flag of the result is not reproduced; nothing here reads it yet.
void cube_2t(const GvEngine& e, const GvRequest& r, const Board& board, const CubeInfo& ci,
             std::ostringstream& s) {
    RolloutConfig rc;
    rc.n_trials=360; rc.truncation_depth=7; rc.decision_ply=2; rc.truncation_ply=2;
    rc.n_threads=r.threads; rc.seed=42; rc.late_ply=1; rc.late_threshold=1;
    rc.parallelize_trials=true; rc.cube.ply=2; rc.cube_late.ply=2;
    rc.ultra_late_threshold=9999; rc.cubeful_trial_moves=true; rc.cubeful_late_threshold=0;
    RolloutStrategy rollout(e.base,rc); rollout.set_bearoff_db(&e.db);
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
            case CUBE_2T: cube_2t(*e,r,board,ci,s); break;
        }
        check_budget(); auto text=s.str(); char* out=static_cast<char*>(std::malloc(text.size()+1));
        if(!out) throw std::bad_alloc(); std::memcpy(out,text.c_str(),text.size()+1);
        last_error.clear(); return out;
    } catch(const std::exception& ex) { last_error=ex.what(); return nullptr; }
    catch(...) { last_error="evaluation failed"; return nullptr; }
}
}
