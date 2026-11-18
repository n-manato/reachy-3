import json
import time

from lta_exhibit.script_gen import branch_roles, response_schema, to_script
from lta_exhibit.show import TopicBag
from lta_exhibit.topics import TOPICS, get_topic
from lta_exhibit.votes import VoteLog, VoteSession


def test_vote_debounce_and_winner(monkeypatch):
    s = VoteSession("agents_keys", "r1")
    assert s.cast("left", "A")
    assert not s.cast("left", "B")          # same screen inside 2 s
    assert s.cast("right", "B")
    assert s.cast("center", "B")
    assert not s.cast("center", "Z")        # bad option
    assert s.close() == "B"
    assert not s.cast("left", "A")          # closed


def test_no_votes_and_tie():
    assert VoteSession("x", "r").close() is None
    s = VoteSession("x", "r")
    s.cast("left", "A"); s.cast("right", "C")
    assert s.close() in ("A", "C")


def test_log_roundtrip_and_reset(tmp_path):
    path = tmp_path / "v.jsonl"
    log = VoteLog(path)
    s = VoteSession("agents_keys", "r")
    s.cast("left", "A"); log.record_vote(s, "left", "A")
    log.record_result(s, s.close())
    again = VoteLog(path)
    assert again.tonight("agents_keys")["counts"]["A"] == 1
    assert again.tonight("agents_keys")["runs"] == 1
    again.reset()
    assert VoteLog(path).tonight("agents_keys")["total"] == 0
    out = again.export_csv(tmp_path / "x.csv")
    assert out.read_text().startswith("topic,option")


def test_topic_bag_no_immediate_repeat():
    bag = TopicBag(["a", "b", "c"])
    seq = [bag.next() for _ in range(30)]
    assert all(x != y for x, y in zip(seq, seq[1:]))
    assert set(seq[:3]) == {"a", "b", "c"}


def test_branch_roles_end_with_sage():
    for t in TOPICS:
        for k in "ABC":
            roles = branch_roles(k, t)
            assert roles[-1] == "sage" and len(set(roles)) == 3
            if t["options"][k]["role"] != "sage":
                assert roles[0] == t["options"][k]["role"]


def test_to_script_assigns_fixed_roles():
    line = {"text": "Hello  there", "emotion": "nope", "emotion_at": 5}
    raw = {k: line for k in ("intro", "dilemma", "reveal_A", "reveal_B", "reveal_C", "reveal_none", "close")}
    raw["discussion"] = [line] * 6
    for k in "ABC":
        raw[f"branch_{k}"] = [line] * 3
    script = to_script(get_topic("say_its_ai"), raw, "test")
    seg = script["segments"]
    assert [l["role"] for l in seg["discussion"]] == ["nova", "aegis", "sage", "aegis", "nova", "sage"]
    assert seg["intro"][0]["text"] == "Hello there"
    assert seg["intro"][0]["emotion"] == "thoughtful1" and seg["intro"][0]["emotion_at"] == 0.9
    json.dumps(response_schema())


def test_revived_worker_gets_the_volume_it_missed(monkeypatch):
    """Commands are dropped while a worker is being revived, so the latest
    volume must be re-sent once it is back (else a mute set meanwhile is lost)."""
    import pickle

    from lta_exhibit.robot_controller import RobotController

    rc = RobotController("127.0.0.1", None, "sage")
    rc.volume = 0
    sent = []

    class AliveProcess:
        pid = 1
        def poll(self):
            return None

    monkeypatch.setattr(rc, "restart", lambda: setattr(rc, "_process", AliveProcess()))
    monkeypatch.setattr(rc, "ensure_media", lambda: True)
    monkeypatch.setattr(rc, "_write_raw", lambda payload: sent.append(pickle.loads(payload[4:])))
    rc.ensure_alive()
    rc._revive_thread.join(timeout=5)
    assert ("set_volume", {"volume": 0}) in sent


def test_stopped_controller_is_never_revived():
    from lta_exhibit.robot_controller import RobotController

    rc = RobotController("127.0.0.1", None, "nova")
    rc.stop()
    rc.ensure_alive()
    assert rc._closing and rc._revive_thread is None
