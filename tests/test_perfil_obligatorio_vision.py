import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import vigilador_api
from vision_policy import zona_vision_activa


class PerfilObligatorioVisionTest(unittest.TestCase):
    def test_zona_sin_perfil_no_ejecuta_vision(self):
        zona = {"habilitado": True, "labels": ["person"], "perfil_id": ""}
        self.assertFalse(zona_vision_activa(zona, {}, "person"))

    def test_zona_con_perfil_inexistente_no_ejecuta_vision(self):
        zona = {"habilitado": True, "labels": ["person"], "perfil_id": "ausente"}
        self.assertFalse(zona_vision_activa(zona, {}, "person"))

    def test_zona_con_perfil_valido_ejecuta_vision(self):
        zona = {"habilitado": True, "labels": ["person"], "perfil_id": "entrada"}
        perfiles = {"entrada": {"id": "entrada", "prompt": "vigilar entrada"}}
        self.assertTrue(zona_vision_activa(zona, perfiles, "person"))

    def test_label_no_asignado_no_ejecuta_vision(self):
        zona = {"habilitado": True, "labels": ["person"], "perfil_id": "entrada"}
        perfiles = {"entrada": {"id": "entrada"}}
        self.assertFalse(zona_vision_activa(zona, perfiles, "car"))

    def test_api_rechaza_habilitar_sin_perfil(self):
        cfg = {
            "vision": {
                "perfiles": {},
                "zonas": {"zonaentrada": {
                    "habilitado": False, "labels": ["person"], "perfil_id": ""
                }},
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(cfg), encoding="utf-8")
            with patch.object(vigilador_api, "CONFIG_FILE", str(path)):
                ok, msg = vigilador_api.vision_zona_update(
                    "zonaentrada", habilitado=True, perfil_id=""
                )
        self.assertFalse(ok)
        self.assertIn("perfil", msg.lower())

    def test_api_desactiva_zona_al_quitar_perfil(self):
        cfg = {
            "vision": {
                "perfiles": {"entrada": {"id": "entrada"}},
                "zonas": {"zonaentrada": {
                    "habilitado": True, "labels": ["person"], "perfil_id": "entrada"
                }},
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(cfg), encoding="utf-8")
            with patch.object(vigilador_api, "CONFIG_FILE", str(path)):
                ok, _ = vigilador_api.vision_zona_update(
                    "zonaentrada", habilitado=False, perfil_id=""
                )
                saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(ok)
        self.assertFalse(saved["vision"]["zonas"]["zonaentrada"]["habilitado"])
        self.assertEqual(saved["vision"]["zonas"]["zonaentrada"]["perfil_id"], "")

    def test_config_completa_rechaza_zona_habilitada_sin_perfil(self):
        cfg = {
            "politica": {},
            "vision": {
                "perfiles": {},
                "zonas": {"zonaentrada": {
                    "habilitado": True, "labels": ["person"], "perfil_id": ""
                }},
            }
        }
        ok, msg = vigilador_api.validar_config(cfg)
        self.assertFalse(ok)
        self.assertIn("perfil", msg.lower())

    def test_eliminar_perfil_desactiva_zonas_asignadas(self):
        cfg = {
            "vision": {
                "perfiles": {"entrada": {"id": "entrada"}},
                "zonas": {"zonaentrada": {
                    "habilitado": True, "labels": ["person"], "perfil_id": "entrada"
                }},
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(cfg), encoding="utf-8")
            with patch.object(vigilador_api, "CONFIG_FILE", str(path)):
                ok, _ = vigilador_api.perfil_zona_delete("entrada")
                saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(ok)
        zona = saved["vision"]["zonas"]["zonaentrada"]
        self.assertFalse(zona["habilitado"])
        self.assertEqual(zona["perfil_id"], "")

    def test_zona_nueva_nace_sin_vision(self):
        cfg = {"vision": {"perfiles": {}, "zonas": {}}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(cfg), encoding="utf-8")
            with patch.object(vigilador_api, "CONFIG_FILE", str(path)):
                ok, _ = vigilador_api.vision_zona_add("zona_nueva")
                saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(ok)
        self.assertFalse(saved["vision"]["zonas"]["zona_nueva"]["habilitado"])
        self.assertEqual(saved["vision"]["zonas"]["zona_nueva"]["perfil_id"], "")


if __name__ == "__main__":
    unittest.main()
