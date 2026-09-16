# Backend Development Guidelines

> Concrete architecture and coding standards for `invoice_agent`.

---

## Overview

This directory contains executable technical specifications and conventions for `invoice_agent`. All AI coding assistants and contributors must follow these guidelines when adding features, refactoring, or writing tests.

---

## Guidelines Index

| Guide | Description | Status |
|---|---|---|
| [Directory Structure](./directory-structure.md) | Module responsibilities, file organization, and layout | Ready |
| [Data Models & Storage](./data-models-and-storage.md) | Dataclasses, monetary precision, Excel & file persistence | Ready |
| [Error Handling](./error-handling.md) | Result objects, network retry backoff, and fallback providers | Ready |
| [Quality & Testing](./quality-guidelines.md) | Pytest standards, test isolation, and cross-platform safety | Ready |
| [Logging Guidelines](./logging-guidelines.md) | Log levels, lazy formatting, and token masking | Ready |

---

## Core Principles

1. **Document Reality, Not Ideals**: All specifications reflect the codebase as it actually works.
2. **Zero-Crash Batch Processing**: Corrupt or unreadable files must degrade gracefully without terminating batch jobs.
3. **Strict Financial Precision**: All money math uses `Decimal`; never compare floats for monetary values.
4. **Cross-Platform Compatibility**: Full parity between Windows, macOS, and Linux environments.
