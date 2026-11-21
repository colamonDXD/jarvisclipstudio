"""Per-task B-roll switch: the create form's own On/Off for stock cutaways.

Named by Luffy's product decision (2026-11-12): B-roll becomes its own
per-task switch, independent of the other Auto template effects. The
value travels on the task row (``tasks.broll_enabled``):

- ``None`` (NULL) — the row predates the control, or a non-form task
  creator never sent it: the template default decides (Auto template
  plans B-roll, Plain — just cuts never does).
- ``True`` / ``False`` — the user's explicit choice. It wins in Auto
  template mode; Plain ignores it, because Plain's contract (see
  ``auto_director.template_mode``) is that nothing is inserted, ever —
  no stored value can make a Plain task plan a cutaway.

The resolved value is applied ABOVE cue planning: when it resolves Off,
``build_edl`` is told not to plan, so the EDL carries zero B-roll cues
and no stock provider is ever called for the task. This module never
touches how cues are planned or resolved when the switch is On.

THE LEGACY FLAG (U3 contract): task rows also carry the older
``include_broll`` flag, written by the create form's legacy chip and by
the task-settings route. Two render seams historically ANDed the two
values together, so a task whose explicit switch said On but whose
legacy flag was False (an old client that never sends the chip, a chip
left Off) silently rendered with no B-roll at all — and an explicit Off
was overridden by a legacy True the same way. The contract, resolved by
:func:`resolve_broll_gate` at every seam that consults the legacy flag:

- ``broll_enabled`` set (True or False) — it is AUTHORITATIVE. The
  legacy flag is ignored entirely.
- ``broll_enabled`` never set (NULL) — the legacy flag is honoured, so
  old clients and pre-switch tasks keep exactly the behaviour they
  chose; Plain still resolves Off over it.
- Neither set — the template default decides, as before.

A frontend may therefore retire the legacy chip safely: once a client
sends ``broll_enabled``, the chip's value can no longer change what
renders, and tasks whose switch was never set behave as they always
did at these seams.
"""

from __future__ import annotations

from typing import Optional

from .template_mode import is_plain

_TRUE_STRINGS = ("true", "1", "yes", "on")
_FALSE_STRINGS = ("false", "0", "no", "off")


def validate_broll_enabled(value: object) -> Optional[bool]:
    """Normalise a create-form B-roll choice, or None when absent.

    Mirrors ``validate_template_mode``: a missing value is not an error
    (older clients and non-form task creators simply predate the
    control), but a present value must be a boolean (JSON clients send
    real booleans; the string spellings are accepted for form-style
    callers). The API layer turns the raised error into the 422 it
    already returns for the sibling validators.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_STRINGS:
            return True
        if lowered in _FALSE_STRINGS:
            return False
    raise ValueError(
        "Invalid broll_enabled: expected true or false"
    )


def resolve_broll_enabled(
    template_mode: Optional[str],
    broll_enabled: Optional[bool],
) -> bool:
    """Resolve the stored choice to the one boolean the pipeline obeys.

    Plain — just cuts always resolves Off (its identity is unchanged by
    this switch). Otherwise an explicit stored value wins; NULL falls
    back to the Auto template default, On.
    """
    if is_plain(template_mode):
        return False
    if broll_enabled is not None:
        return bool(broll_enabled)
    return True


def resolve_broll_gate(
    template_mode: Optional[str],
    broll_enabled: Optional[bool],
    include_broll: Optional[bool],
) -> bool:
    """Resolve B-roll at a seam that also sees the legacy flag.

    The one resolution every legacy-flag consumer must use (the module
    docstring states the full U3 contract): an explicit per-task
    ``broll_enabled`` is authoritative and the legacy ``include_broll``
    is ignored; only when the switch was never set (None) is the legacy
    flag honoured — and even then Plain resolves Off over it, and a
    legacy value of None (the caller never saw the flag either) falls
    back to the template default.
    """
    if broll_enabled is not None:
        return resolve_broll_enabled(template_mode, broll_enabled)
    if include_broll is not None:
        return bool(include_broll) and resolve_broll_enabled(
            template_mode, None
        )
    return resolve_broll_enabled(template_mode, None)
