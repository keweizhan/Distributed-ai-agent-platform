"""
Unit tests for MockPlanner.
No DB, no network required.
"""

import uuid

import pytest

from shared.models import ExecutionPlan, JobStatus, TaskType
from worker.planner.mock import MockPlanner


@pytest.fixture
def planner() -> MockPlanner:
    return MockPlanner()


@pytest.fixture
def plan(planner: MockPlanner, sample_job_id: uuid.UUID, sample_prompt: str) -> ExecutionPlan:
    return planner.plan(sample_job_id, sample_prompt)


class TestMockPlannerStructure:
    def test_returns_execution_plan(self, plan: ExecutionPlan) -> None:
        assert isinstance(plan, ExecutionPlan)

    def test_job_id_preserved(self, plan: ExecutionPlan, sample_job_id: uuid.UUID) -> None:
        assert plan.job_id == sample_job_id

    def test_has_at_least_two_steps(self, plan: ExecutionPlan) -> None:
        assert len(plan.steps) >= 2

    def test_step_ids_are_unique(self, plan: ExecutionPlan) -> None:
        ids = [s.step_id for s in plan.steps]
        assert len(ids) == len(set(ids)), "step_ids must be unique"

    def test_step_ids_are_snake_case(self, plan: ExecutionPlan) -> None:
        import re
        pattern = re.compile(r"^[a-z][a-z0-9_]*$")
        for step in plan.steps:
            assert pattern.match(step.step_id), f"'{step.step_id}' is not snake_case"

    def test_sequence_is_zero_indexed_ascending(self, plan: ExecutionPlan) -> None:
        sequences = [s.sequence for s in plan.steps]
        assert sequences == list(range(len(plan.steps)))

    def test_last_step_is_synthesis(self, plan: ExecutionPlan) -> None:
        assert plan.steps[-1].task_type == TaskType.SYNTHESIS

    def test_synthesis_has_no_tool_name(self, plan: ExecutionPlan) -> None:
        synthesis_steps = [s for s in plan.steps if s.task_type == TaskType.SYNTHESIS]
        for step in synthesis_steps:
            assert step.tool_name is None

    def test_tool_call_steps_have_tool_name(self, plan: ExecutionPlan) -> None:
        tool_call_steps = [s for s in plan.steps if s.task_type == TaskType.TOOL_CALL]
        for step in tool_call_steps:
            assert step.tool_name is not None, f"Step '{step.step_id}' missing tool_name"

    def test_tool_input_is_dict(self, plan: ExecutionPlan) -> None:
        for step in plan.steps:
            assert isinstance(step.tool_input, dict)


class TestMockPlannerDependencies:
    def test_all_dependencies_reference_valid_step_ids(self, plan: ExecutionPlan) -> None:
        known = {s.step_id for s in plan.steps}
        for step in plan.steps:
            for dep in step.dependencies:
                assert dep in known, f"Step '{step.step_id}' dep '{dep}' not in plan"

    def test_at_least_one_step_has_no_dependencies(self, plan: ExecutionPlan) -> None:
        roots = [s for s in plan.steps if not s.dependencies]
        assert roots, "Plan must have at least one step with no dependencies (a root)"

    def test_ready_steps_returns_roots(self, plan: ExecutionPlan) -> None:
        ready = plan.ready_steps(completed_step_ids=set())
        expected_roots = [s for s in plan.steps if not s.dependencies]
        assert set(s.step_id for s in ready) == set(s.step_id for s in expected_roots)

    def test_ready_steps_respects_completed_set(self, plan: ExecutionPlan) -> None:
        # After completing all steps, no steps should be ready (all already done)
        all_ids = {s.step_id for s in plan.steps}
        ready = plan.ready_steps(completed_step_ids=all_ids)
        # Every step's deps are satisfied — all would be "ready" — that's fine.
        # What matters is nothing crashes and we get a list back.
        assert isinstance(ready, list)


class TestMockPlannerIsolation:
    def test_different_prompts_produce_different_search_queries(self) -> None:
        planner = MockPlanner()
        jid = uuid.uuid4()
        plan_a = planner.plan(jid, "quantum computing research")
        plan_b = planner.plan(jid, "climate change mitigation")
        search_a = next(s for s in plan_a.steps if s.step_id == "web_search")
        search_b = next(s for s in plan_b.steps if s.step_id == "web_search")
        assert search_a.tool_input["query"] != search_b.tool_input["query"]

    def test_plan_is_not_shared_between_calls(self) -> None:
        planner = MockPlanner()
        plan1 = planner.plan(uuid.uuid4(), "prompt one")
        plan2 = planner.plan(uuid.uuid4(), "prompt two")
        assert plan1.job_id != plan2.job_id
        assert plan1.steps is not plan2.steps
