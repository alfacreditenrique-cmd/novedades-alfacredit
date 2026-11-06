# Infrastructure and Architecture for Admin Management Testing

## Overview
This document specifies the test harness, execution environment, isolation protocols, and 4-tier test architecture for the **Panel de Administración Exclusivo para Rol Admin** of the AlfaCredit S.A. Novedades y Combustible platform.

## Test Environment & Architecture
- **Framework**: `pytest` (v9+) with `fastapi.testclient.TestClient`.
- **Database Engine**: SQLite with direct connection verification via `app.database.get_db_connection()`.
- **Session Simulation**: Cookie-based authentication tokens signed via SHA-256 (`create_session_token(user_id, email)`).
- **Environment Flags**: `DISABLE_CLOUDSYNC="1"` is exported during all test runs to ensure tests execute hermetically in local isolation without polluting cloud databases.
- **Config Management**: Automatic backup and restoration of `config.json` before and after test executions.

---

## Strict Isolation & Zero-State-Pollution Protocol
A critical architectural constraint exists in the baseline regression test suite:
- `test_platform.py::test_01_database_initialization` strictly asserts `suc_count == 8`.
- Any lingering records created by administrative testing would fail the baseline regression suite.

### Isolation Rules:
1. **Database Teardown**:
   Every test that creates a branch or user must execute comprehensive cleanup in `finally` blocks or `tearDown`:
   - Delete associated records from `colaboradores` where `sucursal_id` matches test branches.
   - Delete associated records from `detalle_novedades` and `reportes_sucursales` where `sucursal_id` matches test branches.
   - Delete associated records from `recuperacion_tokens`, `eventos_brandon`, and `eventos_dania` for test users.
   - Delete test accounts from `usuarios`.
   - Delete test branches from `sucursales`.
2. **Configuration Teardown**:
   Any test modifying `config.json` (`brandon_email`, `dania_email`) captures original values prior to modification and restores them upon test completion.
3. **Session Cookie Isolation**:
   Clients use fresh session cookies per test case and clear cookies after testing each security principal.

---

## 4-Tier Test Suite Architecture

### Tier 1 — Core Feature Coverage
- **RBAC Enforcement**:
  - `GET /admin/gestion` returns HTTP 403 Forbidden for roles `gerente`, `brandon`, and `jefe_credito`.
  - Unauthenticated requests to `GET /admin/gestion` return redirect (HTTP 302/303 to `/login`) or HTTP 401/403.
  - `GET /admin/gestion` returns HTTP 200 OK for role `admin`.
  - All `/api/admin/...` endpoints return HTTP 403 Forbidden when invoked by non-admin roles.
- **Branch & Manager Creation**:
  - `POST /api/admin/sucursal/crear`: Creates a new branch in `sucursales` and simultaneously provisions a linked `gerente` account in `usuarios` with the specified initial password.
- **Immediate Authentication**:
  - New branch manager authenticates via `/login` using the initial credentials provided during creation.
- **Administrative Password Reset**:
  - `POST /api/admin/usuario/reset-password`: Directly resets a user's password hash in the database, enabling immediate login with the new credentials.
- **User Profile Update**:
  - `POST /api/admin/usuario/editar`: Updates `nombre`, `email`, and `telefono` in the `usuarios` table (and syncs branch contact info).
- **Operational Configuration**:
  - `POST /api/admin/config/referencias`: Updates `brandon_email` and `dania_email` in `config.json`.

### Tier 2 — Boundary & Corner Cases
- **Duplicate Branch Name**: Rejection of creation request when branch name already exists (HTTP 400/422 with descriptive error).
- **Duplicate User Email**: Rejection of user creation or update when email is already registered to another user.
- **Missing Required Fields**: Validation failure for empty branch name, missing manager email, or blank password.
- **Non-Existent User ID**: Password reset or profile update on a nonexistent user returns HTTP 404 or 400.
- **Invalid Email Format**: Parameter validation error when submitting malformed emails for configuration updates.

### Tier 3 — Cross-Feature Combinations
- **Full Operational Lifecycle**:
  1. Admin registers new branch "Sucursal Somoto" with manager "gerente.somoto@alfacreditsa.com" and password "Somoto2026!".
  2. New manager authenticates immediately with initial password.
  3. Admin performs administrative password reset to "SomotoNuevo2026!".
  4. Authentication fails with old password ("Somoto2026!").
  5. Authentication succeeds with new password ("SomotoNuevo2026!").
  6. Admin updates manager details (name: "Gerente Somoto Actualizado", phone: "+505 8888-9999").
  7. Verification of database persistence and session consistency.
  8. Full teardown and verification of database invariants.

### Tier 4 — Real-World Application Scenarios
- **Multi-Branch & Config Reflection**:
  - Admin provisions multiple branches concurrently.
  - Admin updates operational tracking emails in `config.json`.
  - Verification that `get_email_config()` and operational services reflect updated emails.
  - Active cycle report synchronization verification for new branches.
  - Hermetic cleanup returning database to 8 branches and original configuration.

---

## Verification & Execution Commands
To run the automated administrative test suite:
```bash
python -m pytest test_admin_management.py -v
```

To run the complete platform regression suite ensuring zero regression:
```bash
python -m pytest test_platform.py -v
```
