"""修复服务独立拥有临时要求的来源、修订和生命周期规则。"""

import pytest

from autopatch_j.core.repair import models
from autopatch_j.core.repair import service


def test_requirements_use_current_user_text_and_deduplicate(tmp_path):
    repair = service.RepairService(tmp_path)
    initial = repair.start(repair.store.load(), "修复当前方法", ())
    constrained = repair.update_task_requirements(
        initial, ("  只改当前方法  ",), (), "请只改当前方法"
    )
    assert constrained.constraints == ("只改当前方法",)
    assert not initial.constraints
    assert (
        repair.update_task_requirements(
            constrained, ("只改当前方法",), (), "只改当前方法"
        )
        == constrained
    )
    with pytest.raises(models.RepairError, match="原文"):
        repair.update_task_requirements(
            initial, ("改全部文件",), (), "只改当前方法"
        )


def test_requirements_can_be_replaced_or_withdrawn_without_mutating_prior_state(
    tmp_path,
):
    repair = service.RepairService(tmp_path)
    initial = repair.start(repair.store.load(), "修复", ())
    constrained = repair.update_task_requirements(
        initial, ("只改当前方法",), (), "只改当前方法"
    )
    revised = repair.update_task_requirements(
        constrained,
        ("允许修改调用方",),
        ("只改当前方法",),
        "撤回上一条，允许修改调用方",
    )
    assert revised.constraints == ("允许修改调用方",)
    assert constrained.constraints == ("只改当前方法",)
    assert (
        repair.update_task_requirements(
            constrained, (), ("只改当前方法",), "撤回上一条"
        ).constraints
        == ()
    )


@pytest.mark.parametrize(
    ("additions", "removals"),
    [((), ("不存在",)), (("只改当前方法",), ("只改当前方法",)), (("  ",), ())],
)
def test_invalid_requirement_revision_is_rejected(
    tmp_path, additions, removals
):
    repair = service.RepairService(tmp_path)
    initial = repair.start(repair.store.load(), "修复", ())
    constrained = repair.update_task_requirements(
        initial, ("只改当前方法",), (), "只改当前方法"
    )
    with pytest.raises(models.RepairError):
        repair.update_task_requirements(
            constrained, additions, removals, "只改当前方法"
        )
    assert constrained.constraints == ("只改当前方法",)


def test_idle_task_does_not_accept_requirements(tmp_path):
    repair = service.RepairService(tmp_path)
    with pytest.raises(models.RepairError):
        repair.update_task_requirements(
            models.RepairState(), ("约束",), (), "约束"
        )
