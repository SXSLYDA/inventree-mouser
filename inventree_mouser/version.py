# The version of the whole inventree-mouser distribution (the plugin and mouser_lookup) -
# the ONE place it lives. pyproject.toml reads it from here and InvenTree shows it on the
# Plugins page. Don't edit by hand: build-and-bump.ps1 bumps it, and the release commit is
# tagged vX.Y.Z so servers (and OMG Harness) can pin exactly that tag.
PLUGIN_VERSION = "0.1.7"
