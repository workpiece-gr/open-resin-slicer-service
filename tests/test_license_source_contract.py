from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_complete_agpl_text_is_bundled() -> None:
    copying = (ROOT / "COPYING").read_text(encoding="utf-8")
    notice = (ROOT / "LICENSE").read_text(encoding="utf-8")

    assert copying.startswith("GNU AFFERO GENERAL PUBLIC LICENSE\nVersion 3, 19 November 2007\n")
    assert "13. Remote Network Interaction; Use with the GNU General Public License." in copying
    assert "END OF TERMS AND CONDITIONS" in copying
    assert "The complete license text is included in `COPYING`." in notice
    assert "Before the first public release" not in notice


def test_service_offers_public_corresponding_source() -> None:
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")

    assert 'SOURCE_CODE_URL = os.environ.get("SOURCE_CODE_URL", "https://github.com/workpiece-gr/open-resin-slicer-service")' in source
    assert '@app.get("/source")' in source
    assert 'return RedirectResponse(SOURCE_CODE_URL, status_code=307)' in source
