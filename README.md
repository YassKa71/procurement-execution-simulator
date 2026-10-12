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
- Tabbed case workspace: Overview, Requirements & validations, Goods receipts, Digital Invoices, and History.
- Grouped action menus, contextual record actions and in-place form drawers with a full-page fallback.
- Collapsible resource library with image/PDF previews where supported and protected downloads.
- Goods receptions and percentage-based assessments, with explanations for corrections.
- Requirements, human validation requests and recorded decisions with supporting evidence.
- Manually attached emails, supplier invoices and documents.
- Optional validation emails, delivery status and manual retries through a replaceable email interface.
- Internal Digital Invoices with explicit readiness confirmation, audited revisions and simulated payments.
- Searchable invoice register with status, supplier, agreement and overdue filters.
- Agreement closing and reopening with cancellation of outstanding requirements and validations.

The application is under active development. Deadline notifications and urgency summaries are planned. No AI or real banking integration is implemented.

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

## Case workflow

Open an agreement to record goods received, attach supporting resources and track outstanding work. A requirement stores a note, deadline and status; a validation request extends it with a named recipient, question and response. Recording approval or rejection completes that requirement. Rejection remains visible and any follow-up work is recorded as a new requirement.

Goods assessments are separate human actions. An unassessed receipt has no percentage; an assessed receipt can be anywhere from 0% to 100% valid. Correcting an assessment requires an explanation and retains the previous value in the timeline. Validation responses do not automatically assess goods or authorize payment.

All operational writes require an open agreement. Closed cases remain readable and their attachments remain downloadable. Supplier invoices are external evidence, separate from internal Digital Invoices that define amounts payable.

Validation requests can be recorded without sending email. When sending is selected, delivery happens after the request commits; success or failure appears in the case, and failed requests can be retried. Local development uses Django's console email backend. Configure an appropriate Django email backend and `DJANGO_DEFAULT_FROM_EMAIL` for a real provider; mailbox synchronization is outside the scope of this application.

## Invoice and closing workflow

Create a draft Digital Invoice inside an open agreement, describing the portion of the exchange covered, its amount, currency, calculation and due date. A Manager or Bookkeeper explicitly marks it **Ready to pay** after reviewing the supporting evidence. Outstanding requirements do not automatically prevent a justified partial payment.

Executing a **simulated payment** records a unique payment reference and financial snapshot, then marks the invoice **Paid** in the same transaction. No money is transferred. Paid and cancelled invoices cannot be edited. Draft and ready invoices can be cancelled; a ready invoice can return to draft. Changing any editable invoice detail resets readiness. The associated agreement is fixed; cancel a misplaced invoice and create a replacement in the correct case. Stale forms require a fresh review before any update or payment.

An agreement can close as **Completed** or **Cancelled**. Ready-to-pay invoices block closure until paid, cancelled or returned to draft. Draft invoices and unassessed receipts do not block closure. Closing cancels all outstanding requirements and pending validations, with individual audit events. Reopening permits further work but does not reactivate cancelled items.

## Roles

| Action | Bookkeeper | Manager |
| --- | :---: | :---: |
| View commercial records and download documents | Yes | Yes |
| Create Exchange Agreements | Yes | Yes |
| Create, edit and delete unused POs | Yes | Yes |
| Operate open cases: receptions, requirements, validations and attachments | Yes | Yes |
| Create and revise invoices, confirm readiness and simulate payments | Yes | Yes |
| Close and reopen agreements | Yes | Yes |
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

The application is a modular monolith. Views handle HTTP and form input; services own business actions. PostgreSQL constraints prevent missing commercial bases and duplicate PO associations. Row locks coordinate agreement creation with record maintenance. Operational, billing and closing actions lock the agreement before its child records, so concurrent actions respect lifecycle boundaries. Database uniqueness protects against duplicate payments; invoice revisions reject stale form submissions.

| Module | Responsibility |
| --- | --- |
| `accounts` | Authentication and business permissions |
| `suppliers` | Supplier records |
| `contracts` | Contracts, POs and internal policies |
| `agreements` | Exchange Agreements and case pages |
| `operations` | Goods receipts, requirements, validations and case resources |
| `billing` | Internal invoices and simulated payment execution |
| `audit` | Persistent business history |
| `config` | Settings and routing |

`notifications` is reserved for a later development stage. The service boundary also leaves room for a future agent adapter to use the same actions and controls.

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

The PostgreSQL suite checks permission boundaries, business validation, audit rollback, protected records, document access, requirement/validation state transitions, goods assessments, upload cleanup on rollback, invoice state transitions, simulated payments, closure rollback, stale forms, and concurrent PO conversion, validation responses, payment attempts and closing/readiness actions. Tests use a separate database; the configured PostgreSQL role needs `CREATEDB` permission.

```sh
python manage.py test --settings=config.postgres_test_settings
python manage.py check
python manage.py makemigrations --check --dry-run
```

With Docker, prefix each command with `docker compose run --rm web`. An SQLite fallback is available through `--settings=config.test_settings`; PostgreSQL concurrency tests are skipped in that configuration.

## Deployment notes

The Docker image runs Gunicorn; local Compose uses Django's development server. Production requires PostgreSQL, persistent file storage and HTTPS. Configure a unique secret, explicit allowed hosts and `DJANGO_DEBUG=false`; run migrations, role setup, `collectstatic --noinput` and `check --deploy` during deployment.

Files use Django storage abstractions and authenticated download views. Do not expose uploaded media as a public directory. Documents referenced by audit snapshots are retained when unused records are updated or deleted.
