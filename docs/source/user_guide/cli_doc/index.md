(CLI-usage)=
# CLI Usage Guide

The FABulous command-line interface (CLI) provides a set of tools for generating eFPGA fabric projects directly from your terminal. This guide covers common commands, options, and typical workflows to help you get started quickly with fabric generation and project synthesis.

## Configuration Management

### Opening the Global Configuration File

You can edit the global FABulous configuration file using the `config` command:

```bash
FABulous config
```

This command opens the global `.env` file located in your FABulous user configuration directory (typically `~/.config/FABulous/.env` on Linux/macOS or `%APPDATA%\FABulous\.env` on Windows).

The editor used is determined by the following order of precedence:
1. `FAB_DEFAULT_EDITOR` environment variable
2. `VISUAL` environment variable
3. `EDITOR` environment variable
4. Falls back to `nano` if none are set

**Example:**
```bash
# Set your preferred editor
export FAB_DEFAULT_EDITOR=vim

# Open the config file
FABulous config
```

If the global `.env` file doesn't exist, it will be created automatically when you run this command.

```{toctree}
fabulous_variable
```
