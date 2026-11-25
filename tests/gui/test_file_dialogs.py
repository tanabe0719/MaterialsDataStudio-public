from pathlib import Path

from PySide6.QtWidgets import QFileDialog as QtFileDialog

from materials_data_studio.ui.file_dialogs import QFileDialog


def test_open_save_and_folder_pickers_share_last_selection(monkeypatch, tmp_path):
    imported, exported = (tmp_path / name for name in ("import", "export"))
    imported.mkdir()
    exported.mkdir()
    monkeypatch.setattr(QtFileDialog, "getOpenFileNames",
                        lambda *_: ([str(imported / "one.xy"), str(imported / "two.xy")], "XRD"))
    assert QFileDialog.getOpenFileNames()[1] == "XRD"

    def save(_parent, _caption, filename, *_args, **_kwargs):
        assert Path(filename) == imported / "figure.svg"
        assert _kwargs["options"] == QtFileDialog.Option.DontConfirmOverwrite
        return str(exported / "figure.svg"), "SVG"

    monkeypatch.setattr(QtFileDialog, "getSaveFileName", save)
    assert QFileDialog.getSaveFileName(None, "Export", str(tmp_path / "figure.svg"),
                                      options=QtFileDialog.Option.DontConfirmOverwrite)[1] == "SVG"

    def choose(_parent, _caption, folder, *_args):
        assert Path(folder) == exported
        return str(imported)

    monkeypatch.setattr(QtFileDialog, "getExistingDirectory", choose)
    assert Path(QFileDialog.getExistingDirectory()) == imported

    def cancel(_parent, _caption, folder, *_args):
        assert Path(folder) == imported
        return "", ""

    monkeypatch.setattr(QtFileDialog, "getOpenFileName", cancel)
    assert QFileDialog.getOpenFileName() == ("", "")
    assert QFileDialog.getOpenFileName() == ("", "")


def test_missing_remembered_folder_uses_original_default(monkeypatch, tmp_path):
    old = tmp_path / "removed"
    old.mkdir()
    monkeypatch.setattr(QtFileDialog, "getExistingDirectory", lambda *_: str(old))
    QFileDialog.getExistingDirectory()
    old.rmdir()

    def save(_parent, _caption, name, *_args):
        assert name == "experiment.mdsg"
        return "", ""

    monkeypatch.setattr(QtFileDialog, "getSaveFileName", save)
    assert QFileDialog.getSaveFileName(None, "Save as", "experiment.mdsg") == ("", "")
