"""Compute an Annet patch offline, the way `annet patch` does, with our rulebook.

`annet file-patch` cannot do this for CE rules: it reduces --hw to the vendor, and
some built-in Huawei rules only apply when the hardware is a CE switch.
"""

from pathlib import Path

from annet import rulebook
from annet.annlib.netdev.views.hardware import HardwareView
from annet.annlib import patching
from annet.api import _format_patch_blocks, patch_from_pre
from annet.lib import get_context
from annet.vendors import registry_connector, tabparser

CONTEXT = Path(__file__).resolve().parents[2] / "deploy" / "annet" / "context.yml"
MODEL_HW = "Huawei CE6881H-48S6CQ"



def patch(old: str, new: str, hw_model: str = MODEL_HW) -> str:
    hw = HardwareView(hw_model, "")
    formatter = registry_connector.get().match(hw).make_formatter()
    old_tree = tabparser.parse_to_tree(old, formatter.split)
    new_tree = tabparser.parse_to_tree(new, formatter.split)
    # As `annet patch` does (annet.api._diff_and_patch): unchanged rows stay in the pre, so patch
    # logic sees an interface's unchanged `ip binding vpn-instance` (rulebook.huawei.iface).
    rb = rulebook.get_rulebook(hw)
    pre = patching.make_pre(patching.make_diff(old_tree, new_tree, rb, []))
    patch_tree = patch_from_pre(pre, hw, rb, False)
    return _format_patch_blocks(patch_tree, hw, " ")


def use_our_rulebook(monkeypatch) -> None:
    monkeypatch.setenv("ANN_CONTEXT_CONFIG_PATH", str(CONTEXT))
    get_context.cache_clear()
    _clear_rulebook_cache()


def _clear_rulebook_cache() -> None:
    # The provider reads the rulebook module from the context once and caches loaded
    # rulebooks per hardware for the life of the process.
    provider = rulebook.rulebook_provider_connector.get()
    provider.rulebook_module = get_context()["rulebook"]["module"]
    provider._rulebook_cache.clear()
    provider._rulebook_text_cache.clear()
