import tempfile
import unittest
from pathlib import Path

from incidents import IncidentStore


class IncidentStoreTests(unittest.TestCase):
    def test_incident_is_opened_resolved_and_kept_in_history(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "incidents.db"
            store = IncidentStore(database_path)
            store.initialize()
            try:
                opened = store.record_transition("1001", "offline", now=100)
                self.assertEqual(opened["status"], "open")
                self.assertEqual(opened["duration_seconds"], 0)

                resolved = store.record_transition("1001", "online", now=145)
                self.assertEqual(resolved["status"], "resolved")
                self.assertEqual(resolved["duration_seconds"], 45)
            finally:
                store.close()

            restarted_store = IncidentStore(database_path)
            restarted_store.initialize()
            try:
                history = restarted_store.recent(now=145)
                self.assertEqual(len(history), 1)
                self.assertEqual(history[0]["id"], opened["id"])
            finally:
                restarted_store.close()

    def test_repeated_offline_event_does_not_duplicate_open_incident(self):
        with tempfile.TemporaryDirectory() as directory:
            store = IncidentStore(Path(directory) / "incidents.db")
            store.initialize()
            try:
                first = store.record_transition("1001", "offline", now=100)
                repeated = store.record_transition("1001", "offline", now=110)
                self.assertEqual(repeated["id"], first["id"])
                self.assertEqual(len(store.open_by_extension(now=110)), 1)
            finally:
                store.close()

    def test_removed_extension_closes_open_incident_with_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            store = IncidentStore(Path(directory) / "incidents.db")
            store.initialize()
            try:
                store.record_transition("1001", "offline", now=100)
                changed = store.resolve_removed_extensions({"1001"}, now=125)
                incident = store.recent(now=125)[0]
                self.assertEqual(changed, 1)
                self.assertEqual(incident["status"], "resolved")
                self.assertEqual(incident["resolution_reason"], "removed")
                self.assertEqual(incident["duration_seconds"], 25)
            finally:
                store.close()


class _CalendarDeDuasJanelas:
    """Expediente ficticio: instantes 0-100 e 200-300 sao horario util."""

    configured = True
    JANELAS = ((0, 100), (200, 300))

    def is_working_time(self, timestamp: float) -> bool:
        return any(inicio <= timestamp < fim for inicio, fim in self.JANELAS)

    def working_seconds(self, start: float, end: float) -> float:
        total = 0.0
        for inicio, fim in self.JANELAS:
            total += max(0.0, min(end, fim) - max(start, inicio))
        return total


class IncidentCalendarTests(unittest.TestCase):
    def test_queda_fora_do_expediente_nao_vira_incidente(self):
        with tempfile.TemporaryDirectory() as directory:
            store = IncidentStore(Path(directory) / "incidents.db", _CalendarDeDuasJanelas())
            store.initialize()
            try:
                # 150 cai no vao entre as duas janelas: empresa fechada.
                self.assertIsNone(store.record_transition("1001", "offline", now=150))
                self.assertEqual(store.recent(now=150), [])
            finally:
                store.close()

    def test_duracao_conta_apenas_o_tempo_dentro_do_expediente(self):
        with tempfile.TemporaryDirectory() as directory:
            store = IncidentStore(Path(directory) / "incidents.db", _CalendarDeDuasJanelas())
            store.initialize()
            try:
                # Cai as 90 (util), volta as 250: 160 segundos corridos, mas so
                # 10 antes do fim da janela + 50 depois da reabertura = 60 uteis.
                store.record_transition("1001", "offline", now=90)
                resolvido = store.record_transition("1001", "online", now=250)
                self.assertEqual(resolvido["duration_seconds"], 60)
            finally:
                store.close()

    def test_extensao_removida_tambem_conta_so_o_tempo_util(self):
        with tempfile.TemporaryDirectory() as directory:
            store = IncidentStore(Path(directory) / "incidents.db", _CalendarDeDuasJanelas())
            store.initialize()
            try:
                # Mesmo cenario do teste de resolucao normal (90 -> 250, 60
                # uteis), mas fechado por remocao do ramal do monitoramento.
                store.record_transition("1001", "offline", now=90)
                changed = store.resolve_removed_extensions({"1001"}, now=250)
                incident = store.recent(now=250)[0]
                self.assertEqual(changed, 1)
                self.assertEqual(incident["resolution_reason"], "removed")
                self.assertEqual(incident["duration_seconds"], 60)
            finally:
                store.close()

    def test_sem_calendario_o_historico_segue_registrando_sempre(self):
        with tempfile.TemporaryDirectory() as directory:
            store = IncidentStore(Path(directory) / "incidents.db")
            store.initialize()
            try:
                aberto = store.record_transition("1001", "offline", now=150)
                self.assertIsNotNone(aberto)
                resolvido = store.record_transition("1001", "online", now=250)
                self.assertEqual(resolvido["duration_seconds"], 100)
            finally:
                store.close()
