import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import vigilador_db


class ReclasificacionVisionTest(unittest.TestCase):
    def test_gato_detectado_por_vision_corrige_label_person(self):
        veredicto = {
            "tipo": "otro",
            "descripcion": "No se observa ninguna persona; solo un animal, posiblemente un gato negro.",
            "objetos": [],
            "confianza": 1.0,
        }
        self.assertEqual(vigilador_db.label_corregido_por_vision("person", veredicto), "cat")

    def test_persona_real_conserva_label_person(self):
        veredicto = {
            "tipo": "repartidor",
            "descripcion": "Se observa una persona entregando un paquete.",
            "objetos": ["paquete"],
            "confianza": 0.98,
        }
        self.assertEqual(vigilador_db.label_corregido_por_vision("person", veredicto), "person")

    def test_fragmento_cat_dentro_de_rescate_no_reclasifica(self):
        veredicto = {
            "tipo": "otro",
            "descripcion": "No hay persona; se observa equipo de rescate abandonado.",
            "objetos": [],
            "confianza": 0.95,
        }
        self.assertEqual(vigilador_db.label_corregido_por_vision("person", veredicto), "person")

    def test_actualizacion_tardia_corrige_veredicto_y_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "vigilador.db")
            with patch.object(vigilador_db, "DB", db_path):
                vigilador_db.init()
                vigilador_db.insertar_avistamiento({
                    "evento_id": "evento-gato",
                    "camara": "parque",
                    "label": "person",
                    "inicio": 1,
                    "fin": 2,
                    "duracion": 1,
                    "zonas": ["zonaparque"],
                    "foto": "/tmp/gato.jpg",
                })
                veredicto = {
                    "tipo": "otro",
                    "descripcion": "No hay persona; se ve un gato negro.",
                    "objetos": [],
                    "confianza": 1.0,
                }
                self.assertEqual(
                    vigilador_db.actualizar_veredicto_avistamiento("evento-gato", veredicto), 1
                )
                con = sqlite3.connect(db_path)
                label, guardado = con.execute(
                    "SELECT label, veredicto FROM avistamientos WHERE evento_id=?",
                    ("evento-gato",),
                ).fetchone()
                con.close()

        self.assertEqual(label, "cat")
        self.assertEqual(json.loads(guardado)["label_original"], "person")


if __name__ == "__main__":
    unittest.main()
