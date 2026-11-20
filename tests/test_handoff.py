import dataclasses
import unittest
from pathlib import Path

from handoff import desk, evidence, options, scenarios
from handoff.model import Receipt, load, parse_time
from handoff.routes import (
    Network,
    completion_of,
    enumerate_routes,
    independent,
    meets_deadline,
    schedule_route,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def t(text):
    return parse_time(text)


class Routes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load(FIXTURES)
        cls.net = Network.build(cls.data)

    def route_ids(self, service_id):
        service = self.data.services[service_id]
        return {
            tuple(edge.id for edge in route)
            for route in enumerate_routes(self.net, service)
        }

    def test_permission_filters_routes(self):
        # 需保密的 S1 不能走普通权限的 L3/L4。
        self.assertEqual(self.route_ids("S1"), {("L1", "L2"), ("L5", "L6")})

    def test_deadline_is_strict(self):
        service = self.data.services["S1"]
        route = next(
            route
            for route in enumerate_routes(self.net, service)
            if route[0].id == "L5"
        )
        traversals = schedule_route(self.net, service, route)
        self.assertEqual(completion_of(traversals), t("18:18"))
        # 须在 deadline 前签收：恰好在截止时刻完成不算。
        self.assertFalse(meets_deadline(service, t("18:18")))
        self.assertTrue(meets_deadline(service, t("18:17")))

    def test_s1_has_single_timely_route(self):
        service = self.data.services["S1"]
        timely = [
            route
            for route in enumerate_routes(self.net, service)
            if (trav := schedule_route(self.net, service, route))
            and meets_deadline(service, completion_of(trav))
        ]
        self.assertEqual(len(timely), 1)
        self.assertEqual([edge.id for edge in timely[0]], ["L1", "L2"])

    def test_s4_has_no_route(self):
        service = self.data.services["S4"]
        for route in enumerate_routes(self.net, service):
            self.assertIsNone(schedule_route(self.net, service, route))

    def test_independence_requires_no_shared_channel_or_gateway(self):
        service = self.data.services["S2"]
        factors = {
            tuple(edge.id for edge in route): self.net.factors_of(route)
            for route in enumerate_routes(self.net, service)
        }
        # 经过不同人但共用 TELL-A 与关口 P1：不算独立备援。
        self.assertFalse(independent(factors[("L1", "L2")], factors[("L3", "L4")]))
        self.assertTrue(independent(factors[("L1", "L2")], factors[("L5", "L6")]))


class Scenarios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load(FIXTURES)
        cls.baseline, cls.impacts = scenarios.factor_impacts(cls.data)
        cls.by_factor = {
            (factor.kind, factor.name): (result, lost)
            for factor, result, lost in cls.impacts
        }

    def test_baseline_all_but_s4_met(self):
        outcomes = self.baseline.outcomes
        self.assertEqual(outcomes["S4"].status, scenarios.UNREACHABLE)
        for service_id in ("S1", "S2", "S3", "S5", "S6"):
            self.assertEqual(outcomes[service_id].status, scenarios.MET, service_id)

    def test_shared_occupancy_in_one_scenario(self):
        # 同一情景下共用同一份占用事实：S6 排在 S5 占用 U6 之后。
        self.assertEqual(self.baseline.outcomes["S6"].completion, t("19:43"))
        self.assertEqual(self.baseline.outcomes["S2"].completion, t("18:17"))

    def test_channel_outage_loses_promises(self):
        self.assertEqual(self.by_factor[("channel", "TELL-A")][1], ["S1"])
        self.assertEqual(self.by_factor[("channel", "TELL-E")][1], ["S5"])
        self.assertEqual(self.by_factor[("channel", "TELL-D")][1], ["S6"])

    def test_gateway_outage(self):
        # 关口 P1 失效：L1-L4 全部不可用，S1 失去唯一及时路线。
        self.assertEqual(self.by_factor[("gateway", "P1")][1], ["S1"])

    def test_post_outage(self):
        self.assertEqual(self.by_factor[("post", "P4")][1], ["S3"])

    def test_s2_survives_tell_a_via_independent_route(self):
        result, _lost = self.by_factor[("channel", "TELL-A")]
        self.assertEqual(result.outcomes["S2"].status, scenarios.MET)

    def test_adopted_options_change_scenario(self):
        # 采纳 Q2 后，TELL-A 中断下 S1 改走 L5+Q2，仍能赶在截止前。
        result = scenarios.run_scenario(
            self.data,
            scenarios.Factor("channel", "TELL-A"),
            adopted=frozenset({"Q2"}),
        )
        outcome = result.outcomes["S1"]
        self.assertEqual(outcome.status, scenarios.MET)
        self.assertEqual([edge.id for edge in outcome.route], ["L5", "Q2"])
        self.assertEqual(outcome.completion, t("18:14"))

    def test_adopted_q3_covers_tell_e_outage(self):
        result = scenarios.run_scenario(
            self.data,
            scenarios.Factor("channel", "TELL-E"),
            adopted=frozenset({"Q3"}),
        )
        outcome = result.outcomes["S5"]
        self.assertEqual(outcome.status, scenarios.MET)
        self.assertEqual([edge.id for edge in outcome.route], ["Q3"])

    def test_standby_option_occupies_person(self):
        # Q3 全程候命占用 U2：19:25-20:00 内 U2 不能再承接 L10。
        # （TELL-E 正常时 S5 若排在候命窗口内，只能走 Q3 自身。）
        result = scenarios.run_scenario(
            self.data, None, adopted=frozenset({"Q3"})
        )
        outcome = result.outcomes["S5"]
        self.assertEqual(outcome.status, scenarios.MET)
        self.assertEqual([edge.id for edge in outcome.route], ["Q3"])


class Evidence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load(FIXTURES)

    def check(self, link, shift, perm):
        return evidence.link_evidence(
            self.data.drills, link, shift, frozenset({perm})
        )

    def test_known_possible_unknown_failed(self):
        self.assertEqual(self.check("L1", "day", "需保密").status, evidence.KNOWN)
        possible = self.check("L6", "day", "需保密")
        self.assertEqual(possible.status, evidence.POSSIBLE)
        self.assertEqual(possible.interval, (t("18:12"), t("18:22")))
        self.assertEqual(self.check("L1", "night", "需保密").status, evidence.UNKNOWN)
        self.assertEqual(self.check("L10", "night", "需保密").status, evidence.FAILED)

    def test_day_confirmation_does_not_cover_night(self):
        # L2 白班已知，夜班无任何记录。
        self.assertEqual(self.check("L2", "day", "需保密").status, evidence.KNOWN)
        self.assertEqual(self.check("L2", "night", "需保密").status, evidence.UNKNOWN)

    def test_permission_must_cover(self):
        # L8 夜班普通已知，但需保密只有缺观测声明。
        self.assertEqual(self.check("L8", "night", "普通").status, evidence.KNOWN)
        self.assertEqual(self.check("L8", "night", "需保密").status, evidence.POSSIBLE)

    def test_higher_clearance_covers_lower(self):
        self.assertEqual(self.check("L1", "day", "普通").status, evidence.KNOWN)


class DeskFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load(FIXTURES)
        cls.result = desk.Desk(cls.data).run()

    def test_ack_after_withdrawal_does_not_transfer(self):
        state = self.result.states["S1"]
        self.assertEqual(state.owner, "P2")  # 备用接手未确认，原归属不变
        self.assertEqual(state.status, desk.MANUAL)
        self.assertEqual(
            state.reasons,
            [desk.REASON_COMMON_SOURCE, desk.REASON_EVIDENCE, desk.REASON_DEADLINE],
        )
        self.assertEqual(self.result.proposals["R5"].resolution, "withdrawn")

    def test_interrupted_then_too_late_goes_manual(self):
        state = self.result.states["S5"]
        self.assertEqual(state.owner, "P7")
        self.assertEqual(state.status, desk.MANUAL)
        self.assertEqual(
            state.reasons, [desk.REASON_COMMON_SOURCE, desk.REASON_DEADLINE]
        )

    def test_unaffected_service_keeps_going(self):
        state = self.result.states["S3"]
        self.assertEqual(state.owner, "P4")
        self.assertEqual(state.status, desk.OK)

    def _receipt(self, **kwargs):
        defaults = dict(
            id="X", service=None, link=None, channel=None, kind="signed",
            effective=t("18:00"), received=t("18:00"), owner=None, prior=None,
        )
        defaults.update(kwargs)
        return Receipt(**defaults)

    def test_standby_slot_blocks_other_chains(self):
        receipts = [
            self._receipt(id="A1", service="S1", link="L2", kind="signed",
                          owner="P2", effective=t("18:03"), received=t("18:03")),
            self._receipt(id="A2", service="S1", link="L6", kind="handoff_proposed",
                          owner="P4", effective=t("18:06"), received=t("18:06")),
            # 同一人员 P4 的候命时段在未决期间不能再划给别的链。
            self._receipt(id="A3", service="S2", link="L6", kind="handoff_proposed",
                          owner="P4", effective=t("18:07"), received=t("18:07")),
            self._receipt(id="A4", service="S1", kind="handoff_withdrawn",
                          prior="A2", effective=t("18:08"), received=t("18:08")),
            # 撤回释放候命时段后可以再提议。
            self._receipt(id="A5", service="S2", link="L6", kind="handoff_proposed",
                          owner="P4", effective=t("18:09"), received=t("18:09")),
        ]
        data = dataclasses.replace(self.data, receipts=receipts)
        result = desk.Desk(data).run()
        self.assertNotIn("A3", result.proposals)  # 被拒绝，未立提议
        self.assertTrue(result.proposals["A5"].open)

    def test_valid_ack_transfers_ownership(self):
        receipts = [
            self._receipt(id="B1", service="S2", link="L2", kind="signed",
                          owner="P2", effective=t("18:07"), received=t("18:07")),
            self._receipt(id="B2", service="S2", link="L6", kind="handoff_proposed",
                          owner="P4", effective=t("18:08"), received=t("18:08")),
            self._receipt(id="B3", service="S2", kind="handoff_ack",
                          prior="B2", effective=t("18:09"), received=t("18:09")),
            # 已完成交接后的撤回不再生效。
            self._receipt(id="B4", service="S2", kind="handoff_withdrawn",
                          prior="B2", effective=t("18:10"), received=t("18:10")),
        ]
        data = dataclasses.replace(self.data, receipts=receipts)
        result = desk.Desk(data).run()
        state = result.states["S2"]
        self.assertEqual(state.owner, "P4")
        self.assertEqual(state.planned_link, "L6")
        self.assertEqual(state.status, desk.OK)


class Options(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load(FIXTURES)
        cls.comparison = options.compare_options(cls.data)

    def test_trial_slot_conflict(self):
        pairs = {(c.first, c.second) for c in self.comparison.conflicts}
        self.assertIn(("Q1", "Q2"), pairs)

    def test_standby_conversion_consumes_on_site_promise(self):
        costs = {cost.option: cost.reason for cost in self.comparison.costs}
        self.assertIn("Q3", costs)
        self.assertIn("P7", costs["Q3"])

    def test_baseline_not_promisable(self):
        self.assertEqual(
            self.comparison.not_promisable(frozenset()),
            ["S1", "S3", "S4", "S5", "S6"],
        )

    def test_q2_eliminates_s1_common_points(self):
        eliminated = self.comparison.eliminated(frozenset({"Q2"}))
        self.assertEqual(eliminated["S1"], frozenset({"电话 TELL-A", "关口岗位 P1"}))
        # Q1 与 Q2 争同一名额但起不到同样作用。
        self.assertNotIn("S1", self.comparison.eliminated(frozenset({"Q1"})))

    def test_q3_eliminates_s5_common_point(self):
        eliminated = self.comparison.eliminated(frozenset({"Q3"}))
        self.assertEqual(eliminated["S5"], frozenset({"电话 TELL-E"}))

    def test_best_set_still_leaves_unpromisable(self):
        best = self.comparison.not_promisable(frozenset({"Q2", "Q3", "Q4"}))
        self.assertEqual(best, ["S3", "S4", "S6"])


if __name__ == "__main__":
    unittest.main()
