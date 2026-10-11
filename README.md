# Procurement Execution Simulator

A portfolio application exploring how a company can manage supplier exchanges through controlled, traceable business actions.

The central record is an **Exchange Agreement**: one operational case connecting the commercial basis of an exchange to its execution history. A contract describes the supplier relationship; a purchase order describes a specific order; the agreement tracks its execution.

## Current capabilities

- Authenticated workspaces with Manager and Bookkeeper permissions.
- Supplier records, contracts, versioned internal policies and purchase orders.
- Independent PO creation, with an optional agreement created in the same transaction.
- Agreement creation from a contract, an unused PO, or both.
- Searchable commercial record lists, case resources and chronological business events.
- Protected document downloads and audited changes to unused records.

The application is under active development. Goods reception, requirements, validation requests, digital invoices, simulated payments and deadline notifications are planned. No AI or real banking integration is implemented.

## Business rules

| Rule | Behavior |
| --- | --- |
| Commercial basis | Every agreement requires a contract, a PO, or both. |
| PO execution | A PO can be linked to at most one agreement, including closed agreements. |
| Optional creation | The PO form defaults to **No** for creating an associated agreement. |
| Consistency | Suppliers must match across the agreement, contract and PO. |
| Historical integrity | A contract, policy or PO used by an open or closed agreement cannot be edited or deleted through the application. |
| Traceability | Creation, edits and deletions produce persistent audit events. Edits retain before/after snapshots; deletion retains the former record's details. |

A contract referenced through an agreement's PO is also protected. Unused POs can be revised before execution. A contract attached to an unused PO can be edited, but must be unlinked before deletion or a supplier change. Policy changes after use require a new version.

## Roles

| Action | Bookkeeper | Manager |
| --- | :---: | :---: |
| View commercial records and download documents | Yes | Yes |
| Create Exchange Agreements | Yes | Yes |
| Create, edit and delete unused POs | Yes | Yes |
| Create contracts | No | Yes |
| Edit or delete unused contracts | No | Yes |
| Create policy versions; edit or delete unused policies | No | Yes |

Django superusers administer accounts and suppliers and can perform the same business actions. Used records remain protected even for superusers. Admin maintenance routes reuse the business forms and services. Deletion always requires confirmation.

## Architecture

**Python | Django | PostgreSQL | Django templates | Docker Compose**

```text
Django forms and views
         |
Application services: permissions, validation, transactions
         |
PostgreSQL records + business events
```

The application is a modular monolith. Views handle HTTP and form input; services own business actions. PostgreSQL constraints prevent missing commercial bases and duplicate PO associations. Row locks coordinate agreement creation with record maintenance.

| Module | Responsibility |
| --- | --- |
| `accounts` | Authentication and business permissions |
| `suppliers` | Supplier records |
| `contracts` | Contracts, POs and internal policies |
| `agreements` | Exchange Agreements and case pages |
| `audit` | Persistent business history |
| `config` | Settings and routing |

`operations`, `billing` and `notifications` are reserved for the next development stages. The service boundary also leaves room for a future agent adapter to use the same actions and controls.

## Run locally

### Docker Compose

Requires Docker with Compose. Copy `.env.example` to `.env`, then set a secret key and database password. Keep `.env` out of version control.

```sh
docker compose build
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py setup_roles
docker compose run --rm web python manage.py createsuperuser
docker compose up
```

Open **http://localhost:8000**. In `/admin/`, create suppliers and users, and assign the Manager or Bookkeeper group. Business users do not require staff status. Managers can create contracts and policies from the workspace, then either role can create a PO or agreement.

### Python and PostgreSQL

Requires Python 3.11+ and a running PostgreSQL database. Configure `.env` for that database, create a virtual environment and activate it.

```sh
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py setup_roles
python manage.py createsuperuser
python manage.py runserver
```

After pulling schema or permission changes, run `migrate` and `setup_roles` again. Role setup synchronizes the two standard groups; use separate groups for custom permissions. The PO uniqueness migration stops with the affected PO IDs if existing duplicate associations require review.

## Tests

The PostgreSQL suite checks permission boundaries, business validation, audit rollback, protected records, document access and concurrent PO conversion. Tests use a separate database; the configured PostgreSQL role needs `CREATEDB` permission.

```sh
python manage.py test --settings=config.postgres_test_settings
python manage.py check
python manage.py makemigrations --check --dry-run
```

With Docker, prefix each command with `docker compose run --rm web`. An SQLite fallback is available through `--settings=config.test_settings`; PostgreSQL concurrency tests are skipped in that configuration.

## Deployment notes

The Docker image runs Gunicorn; local Compose uses Django's development server. Production requires PostgreSQL, persistent file storage and HTTPS. Configure a unique secret, explicit allowed hosts and `DJANGO_DEBUG=false`; run migrations, role setup, `collectstatic --noinput` and `check --deploy` during deployment.

Files use Django storage abstractions and authenticated download views. Do not expose uploaded media as a public directory. Documents referenced by audit snapshots are retained when unused records are updated or deleted.
