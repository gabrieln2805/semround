"""Exceptions that map to CLI exit codes.

Exit codes are part of the public contract (frozen at v1.0):
0 pass, 1 findings, 2 usage or config error, 3 internal error.
"""

EXIT_PASS = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_INTERNAL = 3


class SemroundError(Exception):
    exit_code = EXIT_INTERNAL


class UsageError(SemroundError):
    """Bad arguments, config or model. The user can fix it."""

    exit_code = EXIT_USAGE


class ModelError(UsageError):
    """The semantic model file is missing, malformed or inconsistent."""


class ConfigError(UsageError):
    """semround.toml is malformed."""


class Unsupported(Exception):
    """The reference compiler cannot answer this query in this version.

    Not an error: the query is reported as skipped with the reason.
    """


class NoPortableExpression(Unsupported):
    """The metric has no OSSIE_SQL_2026 or ANSI_SQL expression, only vendor dialects.

    No converter can translate it without guessing (apache/ossie#462).
    """
