---
description: 'Python version-specific syntax rules for this project'
applyTo: '**/*.py'
---

# Python Syntax Notes

## Target version

This project targets **Python 3.14** (`target-version = "py314"` in pyproject.toml).

## PEP 758: Unparenthesized `except` (3.14+)

`except ExceptionA, ExceptionB:` is **valid Python 3.14 syntax**.
Semantically identical to `except (ExceptionA, ExceptionB):`.
