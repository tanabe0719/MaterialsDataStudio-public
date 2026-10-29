"""Both languages must resolve to distributable, readable rights/source notices."""
from __future__ import annotations

import pytest

from materials_data_studio.ui.licenses import notice_path
from materials_data_studio.ui.manual import UserManualDialog


@pytest.mark.parametrize("language", ["en", "ja"])
def test_bundled_license_notice_is_readable_in_each_language(qtbot, language):
    path = notice_path(language)
    dialog = UserManualDialog(path)
    qtbot.addWidget(dialog)
    text = dialog.browser.toPlainText()
    assert "LGPL" in text and "MPL" in text and "MIT" in text
    assert "CorrespondingSources.zip" in text
    assert "ApplicationSource.zip" in text
