# app/agent/memory/scratchpad.py
class SimpleMemory:
    def __init__(self, max_notes: int = 6, cap: int = 1000):
        self.observations: list[str] = []
        self.max_notes = max_notes
        self.cap = cap

    def add(self, note: str) -> None:
        self.observations.append(note)
        if len(self.observations) > self.cap:
            self.observations = self.observations[-self.cap:]

    def dump(self) -> str:
        if not self.observations:
            return "(no prior observations)"
        return "\n---\n".join(self.observations[-self.max_notes:])
