"""The J-Lens dataset picker only offers built-in corpora present on the host."""

from app.api.routes import datasets


async def test_available_datasets_excludes_unprovisioned_corpora(client, tmp_path, monkeypatch):
    common_voice = tmp_path / "common_voice.csv"
    common_voice.write_text("filename,transcript\nclip.wav,hello\n", encoding="utf-8")
    monkeypatch.setattr(datasets, "DATASET_PATHS", {
        "common-voice": common_voice,
        "ravdess": tmp_path / "missing_ravdess.csv",
        "librispeech-1000": tmp_path / "missing_librispeech.csv",
    })

    response = await client.get("/datasets/available")

    assert response.status_code == 200
    assert response.json() == {"datasets": ["common-voice"]}
