# ShipIt

A shipment tracking API built with Django and Django REST Framework. Staff create
shipments and update their status; every status change is recorded in a tracking
history so the full journey of a package can be replayed.

> Status: early development. A public tracking page, booking flow, and payments are planned (see Roadmap).

## Features

- Auto-generated tracking IDs (`SHIP-XXXXXXXXXX`)
- Automatic status history on every change, from the API, admin, or scripts
- Search, filter, and ordering on the shipment list
- Token authentication; the API is staff-only by default
- Test suite covering models, permissions, and the API

## Tech stack

Python, Django, Django REST Framework, django-filter, SQLite (development)

## Getting started

```bash
git clone https://github.com/FinisherDev/shipit.git
cd shipit
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

export DJANGO_DEBUG=1           # Windows (PowerShell): $env:DJANGO_DEBUG = "1"
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

See `.env.example` for all configuration variables. Outside debug mode
`DJANGO_SECRET_KEY` and `DJANGO_ALLOWED_HOSTS` must be set.

Run the tests with `python manage.py test`.

## API

All endpoints require a staff account. Get a token first:

```bash
curl -X POST http://127.0.0.1:8000/api/auth/token/ \
  -d "username=YOUR_USER" -d "password=YOUR_PASSWORD"
```

Then send it as `Authorization: Token <token>`.

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/token/` | Obtain an auth token |
| GET | `/api/shipments/` | List shipments (`?search=`, `?status=`, `?ordering=`) |
| POST | `/api/shipments/` | Create a shipment |
| GET | `/api/shipments/<tracking_id>/` | Retrieve a shipment |
| PATCH | `/api/shipments/<tracking_id>/` | Update fields, e.g. status |
| GET | `/api/shipments/<tracking_id>/history/` | Status timeline |

Example:

```bash
curl -X PATCH http://127.0.0.1:8000/api/shipments/SHIP-ABC123DEF4/ \
  -H "Authorization: Token <token>" \
  -H "Content-Type: application/json" \
  -d '{"status": "In Transit"}'
```

## Roadmap

- [x] Core tracking API with history
- [x] Authentication and staff-only access
- [x] Richer shipment details and status flow
- [x] Public tracking page
- [x] Booking form and pricing
- [x] Notifications and payments
- [x] Staff dashboard, labels, proof of delivery
