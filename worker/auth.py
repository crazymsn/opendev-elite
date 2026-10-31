"""Server-side identities. Raw credentials are never stored in service config."""
import dataclasses
import hashlib
import hmac
import os
import re
import secrets
from pathlib import Path

ROLES = frozenset({'reviewer','agent','operator','approver','disburser','auditor','admin'})


@dataclasses.dataclass(frozen=True)
class Principal:
    actor: str
    roles: frozenset
    auth_version: int | None = None
    display_name: str | None = None
    project_ids: frozenset = frozenset()

    def allows(self, *roles):
        return bool(self.roles.intersection(roles) or 'admin' in self.roles)


class IdentityStore:
    def __init__(self, config):
        self.users = []
        actors, hashes = set(), set()
        if not isinstance(config,dict) or not isinstance(config.get('users'),list):
            raise ValueError('Service configuration requires a users list')
        for entry in config['users']:
            if not isinstance(entry,dict) or type(entry.get('disabled',False)) is not bool:
                raise ValueError('Identity must be an object with an optional boolean disabled flag')
            actor, digest, roles = entry.get('actor'), entry.get('token_sha256'), entry.get('roles')
            if not isinstance(actor, str) or not re.fullmatch(r'[A-Za-z0-9_.@-]{1,100}', actor) or actor in actors:
                raise ValueError('Service actors must be unique, simple identifiers')
            if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest) or digest in hashes or len(set(digest)) == 1:
                raise ValueError('Configure a unique SHA-256 token digest for each actor')
            if not isinstance(roles, list) or not roles or not all(isinstance(r,str) and r in ROLES for r in roles):
                raise ValueError('Each actor must have supported roles')
            # The executor identity never shares reviewer or financial privileges.
            if 'agent' in roles and set(roles) != {'agent'}:
                raise ValueError('Agent credentials cannot also hold human review/finance roles')
            actors.add(actor); hashes.add(digest)
            if entry.get('disabled') is not True:
                self.users.append((Principal(actor, frozenset(roles)), digest))
        if not self.users:
            raise ValueError('At least one enabled service identity is required')

    def authenticate(self, authorization):
        if not isinstance(authorization,str) or not re.fullmatch(r'Bearer [A-Za-z0-9_-]{32,256}', authorization):
            return None
        token = authorization[7:]
        digest = hashlib.sha256(token.encode()).hexdigest()
        found = None
        for principal, candidate in self.users:
            if hmac.compare_digest(digest, candidate):
                found = principal
        return found

    def by_actor(self, actor):
        return next((principal for principal, _ in self.users if principal.actor == actor), None)


def create_key(actor, roles, secret_path):
    # Write once to an explicitly chosen local path; never print the raw token.
    token = secrets.token_urlsafe(48)
    identity = {'actor': actor, 'roles': roles, 'token_sha256': hashlib.sha256(token.encode()).hexdigest()}
    IdentityStore({'users':[identity]})
    path = Path(secret_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        stream.write(token + '\n')
    return {'identity': identity, 'secret_file': str(path.resolve()),
            'note': 'Protect this file with the OS account ACL; keep it out of source control.'}
