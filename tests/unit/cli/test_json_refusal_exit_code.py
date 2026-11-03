"""A refused `--json` command exits non-zero and says why on stdout.

Under `--json` fm gives up click's standalone mode so it can answer a usage error in JSON. Click
then RETURNS a `typer.Exit`'s code instead of exiting, and a runner that drops that value turns
every refusal into exit 0 with empty stdout -- success, as far as any script is concerned.
"""

import json

import pytest
import typer

from frappe_manager.exceptions import MigrationRequiredError


@pytest.mark.unit
class TestARefusalIsNotSuccess:
    def test_a_returned_exit_code_becomes_a_real_exit(self, monkeypatch):
        """Click hands the code back as a return value here; dropping it reports success."""
        from frappe_manager import main as main_module

        monkeypatch.setattr(main_module, "_argv_asks_for_json", lambda: True)

        class _Command:
            def main(self, standalone_mode: bool = True):
                assert standalone_mode is False
                return 1

        monkeypatch.setattr("typer.main.get_command", lambda _app: _Command())

        with pytest.raises(SystemExit) as exc:
            main_module._run_app(object())

        assert exc.value.code == 1

    def test_a_zero_return_does_not_raise(self, monkeypatch):
        from frappe_manager import main as main_module

        monkeypatch.setattr(main_module, "_argv_asks_for_json", lambda: True)

        class _Command:
            def main(self, standalone_mode: bool = True):
                return 0

        monkeypatch.setattr("typer.main.get_command", lambda _app: _Command())

        main_module._run_app(object())


@pytest.mark.unit
class TestTheMigrationGateAnswersAMachine:
    def test_it_raises_an_fm_error_under_json_rather_than_exiting_silently(self, monkeypatch, tmp_path):
        """The refusal IS the answer to a --json run, so it must travel the error path that
        renders the envelope, not print guidance a machine never reads."""
        from frappe_manager import commands as commands_module

        bench = tmp_path / "mybench.localhost"
        bench.mkdir()
        (bench / "bench_config.toml").write_text("")

        monkeypatch.setattr(commands_module, "CLI_BENCHES_DIRECTORY", tmp_path)
        monkeypatch.setattr(commands_module, "bench_needs_migration", lambda *_a, **_k: True, raising=False)
        monkeypatch.setattr(
            "frappe_manager.migration_manager.bench_migration_state.bench_needs_migration",
            lambda *_a, **_k: True,
        )
        monkeypatch.setattr(
            "frappe_manager.migration_manager.bench_migration_state.get_bench_migration_version",
            lambda *_a, **_k: "0.0.0",
        )

        class _Machine:
            wants_structured_data = True

            def stop(self):
                pass

        monkeypatch.setattr(commands_module, "get_global_output_handler", lambda: _Machine())

        with pytest.raises(MigrationRequiredError) as exc:
            commands_module.check_bench_migration_required("mybench.localhost")

        assert exc.value.exit_code == 1
        assert exc.value.suggestions == ["Run 'fm migrate mybench.localhost'"]

    def test_a_person_still_gets_the_guidance_and_a_typer_exit(self, monkeypatch, tmp_path):
        from frappe_manager import commands as commands_module

        bench = tmp_path / "mybench.localhost"
        bench.mkdir()
        (bench / "bench_config.toml").write_text("")

        monkeypatch.setattr(commands_module, "CLI_BENCHES_DIRECTORY", tmp_path)
        monkeypatch.setattr(
            "frappe_manager.migration_manager.bench_migration_state.bench_needs_migration",
            lambda *_a, **_k: True,
        )
        monkeypatch.setattr(
            "frappe_manager.migration_manager.bench_migration_state.get_bench_migration_version",
            lambda *_a, **_k: "0.0.0",
        )

        said = []

        class _Terminal:
            wants_structured_data = False

            def stop(self):
                pass

            def warning(self, text, **_kw):
                said.append(text)

            def print(self, text, **_kw):
                said.append(text)

        monkeypatch.setattr(commands_module, "get_global_output_handler", lambda: _Terminal())

        with pytest.raises(typer.Exit) as exc:
            commands_module.check_bench_migration_required("mybench.localhost")

        assert exc.value.exit_code == 1
        assert any("migration required" in line.lower() for line in said)
        assert any("fm migrate mybench.localhost" in line for line in said)


@pytest.mark.unit
def test_the_envelope_of_a_refusal_names_the_recovery():
    """What a script actually receives: ok false, and the command to run in `next`."""
    from frappe_manager.output_manager.envelope import json_error_envelope

    error = MigrationRequiredError("Bench migration required: mybench", suggestions=["Run 'fm migrate mybench'"])
    envelope = json_error_envelope(
        error=str(error).split("\n")[0],
        subject=None,
        path=None,
        next_commands=list(error.suggestions),
    )

    assert json.loads(json.dumps(envelope))["ok"] is False
    assert envelope["next"] == ["Run 'fm migrate mybench'"]
