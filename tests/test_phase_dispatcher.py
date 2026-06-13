"""PhaseDispatcher unit tests."""
from __future__ import annotations
import pytest
from loop_antigravity.phase_dispatcher import(
 Phase,PhaseDispatcher as PD,PhaseDispatchResult,Severity as S)

class TestInit:
 def test_d(self):d=PD();assert d.mr==3
 def test_r(self):assert PD(max_convergence_rounds=5).mr==5
 def test_b(self):
  d=PD(budget_per_phase={Phase.PART_1_1.value:0.8})
  assert d._b[Phase.PART_1_1.value]==0.8

class TestClassify:
 @pytest.mark.parametrize("i,e",[
  ([{"severity":"P0"}],S.P0_RED),
  ([{"severity":"P2"},{"severity":"P2"}],S.P2_YELLOW),
  ([{"severity":"P1"}],S.P1_ORANGE),
  ([{"severity":"P0"},{"severity":"P1"}],S.P0_RED),
  ([],S.P2_YELLOW),
 ])
 def test_c(self,i,e):assert PD().classify_severity(i)==e

class TestDispatchPart1:
 def test_112(self):
  r=PD().dispatch([{"severity":"P0"}],Phase.PART_1_1,0,{})
  assert r.target_phase==Phase.PART_1_2
 def test_131(self):
  r=PD().dispatch([{"severity":"P0"}],Phase.PART_1_3,0,{})
  assert r.target_phase==Phase.PART_1_1
 def test_bub(self):
  d=PD()
  for p in(Phase.PART_1_1,Phase.PART_1_2,Phase.PART_1_3):
   r=d.dispatch([{"severity":"P0"}],p,0,{})
   assert r.target_phase.name.startswith("PART_1")

class TestDispatchPart2:
 def test_212(self):
  r=PD().dispatch([{"severity":"P2"}],Phase.PART_2_1,0,{})
  assert r.target_phase==Phase.PART_2_2
 def test_seq(self):
  d=PD()
  r1=d.dispatch([{"severity":"P2"}],Phase.PART_2_1,0,{})
  assert r1.target_phase==Phase.PART_2_2
  r2=d.dispatch([{"severity":"P2"}],Phase.PART_2_2,0,{})
  assert r2.target_phase==Phase.PART_2_3
 def test_28(self):
  r=PD().dispatch([{"severity":"P2"}],Phase.PART_2_8,0,{})
  assert r.target_phase==Phase.PART_2_8 and r.should_terminate

class TestConvergence:
 def test_low(self):
  r=PD(max_convergence_rounds=5).dispatch([],Phase.PART_1_1,2,{})
  assert not r.should_terminate
 def test_done(self):
  r=PD(max_convergence_rounds=3).dispatch([],Phase.PART_1_1,3,{})
  assert r.should_terminate
 def test_p1b(self):
  r=PD(max_convergence_rounds=3).dispatch([{"severity":"P1"}],Phase.PART_1_1,3,{})
  assert not r.should_terminate

class TestDispatch:
 def test_p0r(self):
  r=PD().dispatch([{"severity":"P0"}],Phase.PART_2_1,0,{})
  assert r.target_phase.name.startswith("PART_1")
 def test_p2r(self):
  r=PD().dispatch([{"severity":"P2"}],Phase.PART_1_1,0,{})
  assert r.target_phase.name.startswith("PART_2")
 def test_term(self):
  r=PD(max_convergence_rounds=3).dispatch([],Phase.PART_2_3,3,{})
  assert r.should_terminate
 def test_p1c(self):
  r=PD(max_convergence_rounds=3).dispatch([{"severity":"P1"}],Phase.PART_1_1,3,{})
  assert r.target_phase.name.startswith("PART_2")
 def test_28t(self):
  r=PD().dispatch([{"severity":"P2"}],Phase.PART_2_8,0,{})
  assert r.should_terminate

class TestTransitions:
 def test_val(self):
  r=PD().dispatch([{"severity":"P2"}],Phase.PART_1_3,0,{})
  assert r.target_phase==Phase.PART_2_1
 def test_lp(self):
  r=PD().dispatch([{"severity":"P2"}],Phase.PART_2_8,0,{})
  assert r.target_phase==Phase.PART_2_8
 def test_fwd(self):
  r=PD().dispatch([{"severity":"P2"}],Phase.PART_2_3,0,{})
  assert r.target_phase==Phase.PART_2_4

class TestBudget:
 def test_11(self):
  r=PD().dispatch([{"severity":"P0"}],Phase.PART_1_1,0,{})
  assert r.budget_injection==0.5
 def test_22(self):
  r=PD().dispatch([{"severity":"P2"}],Phase.PART_2_2,0,{})
  assert r.budget_injection==1.0
 def test_cus(self):
  d=PD(budget_per_phase={Phase.PART_1_1.value:0.9})
  r=d.dispatch([{"severity":"P0"}],Phase.PART_1_3,0,{})
  assert r.budget_injection==0.9
