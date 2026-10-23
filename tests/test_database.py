"""Schema migrations and repositories."""

from __future__ import annotations

import pytest

from apeiron.core.errors import AccountError, DatabaseError, InstanceNotFoundError
from apeiron.db.database import LATEST_VERSION, Database
from apeiron.db.models import Account, Instance, ModRecord


def test_migrations_reach_latest(database: Database):
    assert database.current_version() == LATEST_VERSION
    assert database.integrity_check()


def test_migrations_are_idempotent(database: Database):
    assert database.migrate() == LATEST_VERSION
    assert database.migrate() == LATEST_VERSION


def test_newer_schema_is_rejected(tmp_path):
    db = Database(tmp_path / "future.db")
    db.migrate()
    db.connection.execute(f"PRAGMA user_version={LATEST_VERSION + 5}")
    with pytest.raises(DatabaseError):
        Database(tmp_path / "future.db").migrate()
    db.close()


def test_instance_crud(repos, tmp_path):
    repo = repos["instances"]
    instance = Instance(name="Test", mc_version="1.20.1", directory=str(tmp_path / "inst"))
    repo.add(instance)

    loaded = repo.get(instance.id)
    assert loaded.name == "Test"
    assert loaded.mc_version == "1.20.1"
    assert repo.exists(instance.id)

    loaded.name = "Renamed"
    repo.update(loaded)
    assert repo.get(instance.id).name == "Renamed"

    repo.delete(instance.id)
    assert not repo.exists(instance.id)
    with pytest.raises(InstanceNotFoundError):
        repo.get(instance.id)


def test_instance_name_is_unique(repos, tmp_path):
    repo = repos["instances"]
    repo.add(Instance(name="Same", mc_version="1.20.1", directory=str(tmp_path / "a")))
    with pytest.raises(DatabaseError):
        repo.add(Instance(name="Same", mc_version="1.21", directory=str(tmp_path / "b")))
    assert repo.unique_name("Same") == "Same (2)"


def test_instance_tags_and_metadata_roundtrip(repos, tmp_path):
    repo = repos["instances"]
    instance = Instance(
        name="Meta",
        mc_version="1.20.1",
        directory=str(tmp_path / "m"),
        tags=["survival", "中文"],
        metadata={"loaderVersionId": "fabric-loader-0.16.5-1.20.1"},
    )
    repo.add(instance)
    loaded = repo.get(instance.id)
    assert loaded.tags == ["survival", "中文"]
    assert loaded.metadata["loaderVersionId"].startswith("fabric-loader")


def test_mark_played(repos, tmp_path):
    repo = repos["instances"]
    instance = repo.add(Instance(name="P", mc_version="1.20.1", directory=str(tmp_path / "p")))
    repo.mark_played(instance.id, 120)
    loaded = repo.get(instance.id)
    assert loaded.play_count == 1
    assert loaded.total_play_seconds == 120
    assert loaded.last_played_at is not None


def test_account_unique_uuid(repos):
    repo = repos["accounts"]
    repo.add(Account(kind="offline", name="Steve", uuid="uuid-1"))
    with pytest.raises(AccountError):
        repo.add(Account(kind="offline", name="Other", uuid="uuid-1"))


def test_deleting_account_detaches_instances(repos, tmp_path):
    accounts = repos["accounts"]
    instances = repos["instances"]
    account = accounts.add(Account(kind="microsoft", name="Player", uuid="uuid-2", verified=True))
    instance = instances.add(
        Instance(
            name="I", mc_version="1.20.1", directory=str(tmp_path / "i"), account_id=account.id
        )
    )
    accounts.delete(account.id)
    assert instances.get(instance.id).account_id is None


def test_deleting_instance_cascades_mods(repos, tmp_path):
    instances = repos["instances"]
    mods = repos["mods"]
    instance = instances.add(Instance(name="C", mc_version="1.20.1", directory=str(tmp_path / "c")))
    mods.add(ModRecord(instance_id=instance.id, filename="a.jar", kind="mod"))
    assert len(mods.list_for_instance(instance.id)) == 1
    instances.delete(instance.id)
    assert mods.list_for_instance(instance.id) == []


def test_transaction_rollback(database: Database, repos):
    instances = repos["instances"]
    with pytest.raises(RuntimeError), database.transaction():
        instances.add(Instance(name="Tx", mc_version="1.20.1", directory="/tmp/tx"))
        raise RuntimeError("boom")
    assert instances.list() == []


def test_download_task_upsert_and_interruption(repos):
    from apeiron.db.models import DownloadTask

    repo = repos["downloads"]
    task = DownloadTask(id="t1", url="https://example.com/a", destination="/tmp/a")
    repo.upsert(task)
    task.status = "running"
    task.bytes_done = 10
    repo.upsert(task)
    stored = repo.get("t1")
    assert stored.status == "running"
    assert stored.bytes_done == 10

    changed = repo.mark_interrupted_as_failed()
    assert changed == 1
    assert repo.get("t1").status == "failed"


def test_app_state_kv(repos):
    state = repos["state"]
    state.set("last_page", "2")
    assert state.get("last_page") == "2"
    state.set("last_page", "3")
    assert state.get("last_page") == "3"
    assert state.get("missing", "fallback") == "fallback"


def test_launch_repository(repos, tmp_path):
    instances = repos["instances"]
    launches = repos["launches"]
    instance = instances.add(Instance(name="L", mc_version="1.20.1", directory=str(tmp_path / "l")))
    record = launches.start(instance.id, "/tmp/game.log")
    launches.finish(record.id, 0, "exited", 60)
    recent = launches.recent()
    assert recent[0].status == "exited"
    assert recent[0].exit_code == 0
    assert recent[0].duration_seconds == 60
