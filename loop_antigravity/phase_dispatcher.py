"""
相位调度器——根据问题严重级别和收敛状态决定下一阶段目标。

## 调度规则

| 严重级别 | 路由目标 | 说明 |
|---------|---------|------|
| P0_RED (致命) | Part 1 (重新设计) | 架构/设计级致命缺陷，必须回到 Part 1 设计泡重新设计 |
| P1_ORANGE (核心) | 决策树判断 | 收敛计数器达标则进入 Part 2 repair，否则回到 Part 1 |
| P2_YELLOW (质量) | Part 2 (修复) | 局部实现质量问题，在 Part 2 内部修复 |

## Part 1 设计泡 (part_1_1 -> part_1_2 -> part_1_3)

三个子阶段在同一进程调用内链式执行。P0/P1 路由回退时重新进入 Part 1 的第一个子阶段。

## Part 2 实现链 (part_2_1 -> ... -> part_2_8)

八个子阶段按序执行，每个子阶段一次进程调用。P2 路由回退到 part_2_2 (repair mode)。

## 终止条件

当 phase 到达 part_2_8 且收敛计数器 >= max_convergence_rounds 且无活跃 P0/P1 issue 时终止。
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Optional
# ====
class Severity(Enum):
 """问题严重级别枚举。

 - P0_RED: 致命级——架构/设计缺陷，必须回到 Part 1 重新设计。
 - P1_ORANGE: 核心级——影响主要功能的缺陷，需通过决策树判断是设计级还是实现级修复。
 - P2_YELLOW: 质量级——局部实现问题，可在 Part 2 内部修复。
 """
 P0_RED="P0_RED";P1_ORANGE="P1_ORANGE";P2_YELLOW="P2_YELLOW"
class Phase(Enum):
 """工作流阶段枚举。

 Part 1 (设计泡): part_1_1(需求澄清) -> part_1_2(方向研究) -> part_1_3(方案设计)。
 Part 2 (实现链): part_2_1(计划) -> part_2_2(实现) -> part_2_3(审查) -> part_2_4(E2E策略) ->
                   part_2_5(测试计划) -> part_2_6(测试执行) -> part_2_7(审计) -> part_2_8(硬闸终止)。
 """
 PART_1_1="PART_1_1";PART_1_2="PART_1_2";PART_1_3="PART_1_3"
 PART_2_1="PART_2_1";PART_2_2="PART_2_2";PART_2_3="PART_2_3";PART_2_4="PART_2_4"
 PART_2_5="PART_2_5";PART_2_6="PART_2_6";PART_2_7="PART_2_7";PART_2_8="PART_2_8"
@dataclass
class PhaseDispatchResult:
 """相位调度结果数据类。

 Attributes:
     target_phase: 调度目标阶段。
     severity: 当前问题严重级别。
     budget_injection: 本阶段预算注入量（当前固定 0.5/1.0，预留动态预算扩展）。
     reason: 调度决策原因。
     convergence_count: 当前收敛计数器值。
     should_terminate: 是否应终止循环（part_2_8 + 收敛达标）。
     next_action: 用户可读的下一步操作描述。
 """
 target_phase:Phase;severity:Severity;budget_injection:float;reason:str
 convergence_count:int;should_terminate:bool=False;next_action:str=""
# ====
_P1=(Phase.PART_1_1,Phase.PART_1_2,Phase.PART_1_3)
_P2=(Phase.PART_2_1,Phase.PART_2_2,Phase.PART_2_3,Phase.PART_2_4,Phase.PART_2_5,Phase.PART_2_6,Phase.PART_2_7,Phase.PART_2_8)
_B={p.value:0.5 for p in _P1};_B.update({p.value:1.0 for p in _P2})
class PhaseDispatcher:
 """相位调度器——根据问题列表和收敛状态决定下一步进入哪个阶段。

 核心逻辑: 分析活跃问题列表的严重级别 -> 确定路由目标(Part 1 重新设计 / Part 2 修复) ->
           检查收敛条件 -> 决定是否终止循环。

 Attributes:
     mr (int): 触发收敛所需的最大连续无新问题轮数 (max_convergence_rounds)。
     _b (dict): 各阶段的预算注入映射，key=phase.value, value=注入量(float)。
 """

 def __init__(self,max_convergence_rounds:int=3,budget_per_phase:Optional[dict]=None)->None:
  """初始化相位调度器。

  Args:
      max_convergence_rounds: 收敛所需的最大连续无新问题轮数，默认 3。
      budget_per_phase: 可选的自定义阶段预算映射。若为 None，使用默认值（Part 1 各 0.5，Part 2 各 1.0）。
  """
  self.mr=max_convergence_rounds;self._b=budget_per_phase or _B
 def classify_severity(self,issues:list[dict])->Severity:
  """对一组问题列表进行严重级别分类。

  规则: 取最高严重级别——任一 P0 -> P0_RED，任一 P1 -> P1_ORANGE，其余 -> P2_YELLOW。
  空列表视为 P2_YELLOW（无问题）。

  Args:
      issues: 问题字典列表，每个需包含 "severity" 键(P0/P0_RED/P1/P1_ORANGE/P2/P2_YELLOW)。

  Returns:
      Severity: 最高严重级别。
  """
  if not issues:return Severity.P2_YELLOW
  s={i.get("severity","").upper()for i in issues}
  if s&{"P0","P0_RED"}:return Severity.P0_RED
  if s&{"P1","P1_ORANGE"}:return Severity.P1_ORANGE
  return Severity.P2_YELLOW
 def dispatch(self,issues:list[dict],current_phase:Phase,convergence_counter:int,state_summary:dict)->PhaseDispatchResult:
  """主调度入口——根据当前状态决定下一阶段。

  调度决策流程:
  1. classify_severity() 获取最高严重级别
  2. P0_RED -> 强制回到 Part 1 (_r1)
  3. P1_ORANGE -> 收敛计数器 >= mr 则进入 Part 2 repair (_r2)，否则回到 Part 1 (_r1)
  4. P2_YELLOW -> 进入 Part 2 修复 (_r2)
  5. 校验阶段转换合法性 (_vt)，不合法则保持当前阶段
  6. 判断终止条件: part_2_8 或 收敛计数器达标且无活跃问题

  Args:
      issues: 当前活跃问题列表。
      current_phase: 当前所处阶段。
      convergence_counter: 当前收敛计数器值。
      state_summary: 状态摘要字典（预留，目前未使用）。

  Returns:
      PhaseDispatchResult: 包含目标阶段、严重级别、预算、终止标志等信息的调度结果。
  """
  sev=self.classify_severity(issues)
  if sev==Severity.P0_RED:t=self._r1(sev,current_phase)
  elif sev==Severity.P1_ORANGE:t=self._r2(current_phase)if convergence_counter>=self.mr else self._r1(sev,current_phase)
  else:t=self._r2(current_phase)
  if t not in _P1 and not self._vt(current_phase,t,issues):t=current_phase
  stop=t==Phase.PART_2_8
  if self._cc(convergence_counter)and not issues:stop=True
  a=f"进入阶段 {t.value}"
  if t==Phase.PART_2_1:a+=" -- need plan confirm"
  if sev==Severity.P0_RED:a+=" [P0:design]"
  elif sev==Severity.P1_ORANGE:a+=" [P1:route]"
  return PhaseDispatchResult(target_phase=t,severity=sev,budget_injection=self._gb(t),reason=f"sev={sev.value}, cnt={convergence_counter}/{self.mr}",convergence_count=convergence_counter,should_terminate=stop,next_action=a)
 def _r1(self,severity:Severity,current_phase:Phase)->Phase:
  """Part 1 路由——将当前阶段路由到 Part 1 设计泡的下一个子阶段。

  若当前已在 Part 1 中，则推进到下一个子阶段（循环）。
  若当前在 Part 2 中，则回到 Part 1 的起点 (part_1_1)。

  Args:
      severity: 触发此路由的严重级别。
      current_phase: 当前阶段。

  Returns:
      Phase: Part 1 目标子阶段。
  """
  if current_phase not in _P1:return Phase.PART_1_1
  return _P1[(_P1.index(current_phase)+1)%len(_P1)]
 def _r2(self,current_phase:Phase)->Phase:
  """Part 2 路由——将当前阶段路由到 Part 2 实现链的下一个子阶段。

  若当前在 Part 1 中，则进入 Part 2 起点 (part_2_1)。
  若当前在 Part 2 中，则推进到下一个子阶段；已在末尾 (part_2_8) 则保持不变。

  Args:
      current_phase: 当前阶段。

  Returns:
      Phase: Part 2 目标子阶段。
  """
  if current_phase not in _P2:return Phase.PART_2_1
  i=_P2.index(current_phase);return _P2[i+1]if i<len(_P2)-1 else current_phase
 def _cc(self,convergence_counter:int)->bool:
  """检查收敛条件——收敛计数器是否已达到或超过阈值。

  Args:
      convergence_counter: 当前收敛计数器值。

  Returns:
      bool: True 表示收敛达成，可以终止循环。
  """
  return convergence_counter>=self.mr
 def _vt(self,fp:Phase,tp:Phase,issues:list[dict])->bool:
  """校验阶段转换的合法性。

  规则:
  - 同一阶段 -> 合法
  - Part 1 -> Part 1 或 part_2_1 -> 合法
  - Part 2 -> Part 2 且目标不倒退 -> 合法
  - 其他 -> 合法（宽松策略）

  Args:
      fp: from_phase，当前阶段。
      tp: to_phase，目标阶段。
      issues: 活跃问题列表（预留，当前未使用）。

  Returns:
      bool: True 表示转换合法。
  """
  if fp==tp:return True
  if fp in _P1:return tp in _P1 or tp==Phase.PART_2_1
  if fp in _P2:fi=_P2.index(fp);return tp in _P2 and _P2.index(tp)>=fi
  return True
 def _gb(self,phase:Phase)->float:
  """获取指定阶段的预算注入量。

  Args:
      phase: 目标阶段。

  Returns:
      float: 该阶段的预算注入量。默认 Part 1 各 0.5，Part 2 各 1.0。
  """
  return float(self._b.get(phase.value,1.0))
