"""Tests for the morning digest.

The digest is read in three seconds or not at all, so these pin what it leads
with and how much it says, not its wording.
"""

import unittest

import digest
import poller


def gate(task_id="t_1", reason="DD ready"):
    return poller.Gate(task_id, "T", "blocked", reason)


class AgeTests(unittest.TestCase):
    def test_rounds_to_one_unit(self):
        """"blocked 3d" is actionable; the same fact to the minute is not."""
        self.assertEqual("45m", digest.age(45 * 60))
        self.assertEqual("5h", digest.age(5 * 3600))
        self.assertEqual("3d", digest.age(3 * 86400 + 4 * 3600))


class OrderTests(unittest.TestCase):
    def test_oldest_gate_first(self):
        """What has waited longest is what is most likely forgotten."""
        now = __import__("time").time()
        gates = [gate("t_new"), gate("t_old")]
        ordered = digest.oldest_first(gates, {"t_new": now - 60, "t_old": now - 86400})
        self.assertEqual("t_old", ordered[0][0].task_id)


class RenderTests(unittest.TestCase):
    def test_leads_with_what_is_blocked_on_you(self):
        text = digest.render([(gate(), 7200.0)], {"running": 3, "ready": 1})
        self.assertTrue(text.startswith("1 waiting on you"), text)

    def test_says_so_when_nothing_waits(self):
        """Silence is ambiguous: it reads the same as a broken digest."""
        text = digest.render([], {"running": 0, "ready": 0})
        self.assertIn("Nothing waiting on you", text)

    def test_names_the_oldest_age_up_front(self):
        text = digest.render([(gate(), 3 * 86400)], {})
        self.assertIn("oldest 3d", text)

    def test_caps_the_list_and_counts_the_rest(self):
        """A digest that lists forty cards is a page nobody reads."""
        gates = [(gate(f"t_{i}"), float(i)) for i in range(9)]
        text = digest.render(gates, {})
        self.assertIn("and 4 more", text)
        self.assertLessEqual(len(text.splitlines()), 8)

    def test_carries_work_in_flight(self):
        text = digest.render([], {"running": 2, "ready": 5})
        self.assertIn("2 running, 5 queued", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
