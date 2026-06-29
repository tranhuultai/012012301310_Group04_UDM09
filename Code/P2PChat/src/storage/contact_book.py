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
        """Load contacts from disk and return them.

        Returns:
            Dict mapping peer_id → contact record.
        """
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
        """Add or overwrite a contact record for *peer_id*.

        Args:
            peer_id: SHA-256 peer identifier.
            alias: Human-readable display name.
            trust_state: One of the TrustState constants.
            fingerprint: Colon-separated hex fingerprint.
        """
        self.contacts[peer_id] = {
            "peer_id":     peer_id,
            "alias":       alias,
            "trust_state": trust_state,
            "fingerprint": fingerprint,
        }
        self.save_contacts()

    def remove_contact(self, peer_id: str) -> None:
        """Remove the contact for *peer_id*, if it exists.

        Args:
            peer_id: SHA-256 peer identifier.
        """
        if peer_id in self.contacts:
            del self.contacts[peer_id]
            self.save_contacts()

    def get_contact(self, peer_id: str) -> dict | None:
        """Return the contact record for *peer_id*, or None.

        Args:
            peer_id: SHA-256 peer identifier.
        """
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
        """Partially update an existing contact.

        Only non-None keyword arguments are applied.

        Args:
            peer_id: SHA-256 peer identifier.
            alias: New display name, or None to leave unchanged.
            trust_state: New trust state, or None to leave unchanged.
            fingerprint: New fingerprint, or None to leave unchanged.
        """
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
