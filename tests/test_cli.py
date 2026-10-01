from aos.cli import main
from aos.config import load_user_config
from aos.core import AOS


def run(argv, capsys):
    code = main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_init_link_context(repo, home, project, capsys, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: "/bin/aos" if n == "aos" else None)
    assert run(["init", str(repo), "--graphskill", "/gs/python -m graphskill"], capsys)[0] == 0
    assert load_user_config()["graphskill_cmd"] == ["/gs/python", "-m", "graphskill"]
    code, out, _ = run(["link", str(project), "--name", "shop", "--no-graphskill"], capsys)
    assert code == 0 and "shop" in out
    code, out, _ = run(["context", "--project", str(project)], capsys)
    assert code == 0 and "You are the team agent." in out


def test_context_never_fails(home, tmp_path, capsys):
    code, out, _ = run(["context", "--project", str(tmp_path)], capsys)
    assert code == 0
    assert out.startswith("[aos]") and len(out.strip().splitlines()) == 1


def test_context_unlinked(configured, project, capsys):
    code, out, _ = run(["context", "--project", str(project)], capsys)
    assert code == 0 and "not linked" in out


def test_errors_exit_1_with_hint(home, capsys):
    code, _, err = run(["link", "."], capsys)
    assert code == 1 and "aos init" in err


def test_init_rejects_non_repo(home, tmp_path, capsys):
    assert run(["init", str(tmp_path)], capsys)[0] == 1


def test_inbox_flow(configured, capsys):
    pid = AOS(configured).memory_write("org", "add", {"text": "FY starts April."})["staged"]
    code, out, _ = run(["inbox", "list"], capsys)
    assert pid in out
    assert run(["inbox", "show", pid], capsys)[0] == 0
    assert run(["inbox", "apply", pid], capsys)[0] == 0
    assert AOS(configured).memory("org").entries() == ["FY starts April."]
    assert run(["inbox", "reject", pid], capsys)[0] == 1


def test_bad_targets(configured, project, capsys):
    code, _, err = run(["link", str(project), "--targets", "cursor"], capsys)
    assert code == 1 and "cursor" in err


def test_doctor(configured, capsys):
    code, out, _ = run(["doctor"], capsys)
    assert code == 0 and "repo" in out
