"""Contact book persistence — stores peer aliases and trust state."""

from pathlib import Path

from storage.storage_manager import StorageManager


class ContactBook:
    """Persistent contacts keyed by peer_id.

    File layout (data/storage/contacts.json)::

        {
            "<peer_id>": {
                "peer_id":     "...",
                "alias":       "...",
                "trust_state": "VERIFIED",
                "fingerprint": "..."
            }
        }
    """

    def __init__(self) -> None:
        """Initialise and load contacts from disk."""
        self.data_dir      = Path("data/storage")
        StorageManager.ensure_dir(self.data_dir)
        self.contacts_file = self.data_dir / "contacts.json"
        self.contacts: dict = {}
        self.load_contacts()

    # ------------------------------------------------------------------ #
    # Persistence                                                          #
    # ------------------------------------------------------------------ #

    def load_contacts(self) -> dict:
        """Load contacts from disk and return the peer_id -> record dict."""
        self.contacts = StorageManager.load_json(self.contacts_file, {})
        return self.contacts

    def save_contacts(self) -> None:
        """Persist the current contacts dict to disk atomically."""
        StorageManager.save_json(self.contacts_file, self.contacts)

    # ------------------------------------------------------------------ #
    # CRUD                                                                 #
    # ------------------------------------------------------------------ #

    def add_contact(
        self,
        peer_id: str,
        alias: str,
        trust_state: str,
        fingerprint: str,
    ) -> None:
        """Add or overwrite the contact record for peer_id."""
        self.contacts[peer_id] = {
            "peer_id":     peer_id,
            "alias":       alias,
            "trust_state": trust_state,
            "fingerprint": fingerprint,
        }
        self.save_contacts()

    def remove_contact(self, peer_id: str) -> None:
        """Remove the contact for peer_id, if it exists."""
        if peer_id in self.contacts:
            del self.contacts[peer_id]
            self.save_contacts()

    def get_contact(self, peer_id: str) -> dict | None:
        """Return the contact record for peer_id, or None."""
        return self.contacts.get(peer_id)

    def get_all_contacts(self) -> list[dict]:
        """Return all contacts as a list of record dicts."""
        return list(self.contacts.values())

    def update_contact(
        self,
        peer_id: str,
        alias: str | None = None,
        trust_state: str | None = None,
        fingerprint: str | None = None,
    ) -> None:
        """Update only the given fields of an existing contact; None means unchanged."""
        contact = self.contacts.get(peer_id)
        if not contact:
            return
        if alias is not None:
            contact["alias"] = alias
        if trust_state is not None:
            contact["trust_state"] = trust_state
        if fingerprint is not None:
            contact["fingerprint"] = fingerprint
        self.save_contacts()
