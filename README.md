# Options Trade Tracker

A self-hosted web application to track options trades, monitor their value over time via the Tradier API, and visualize premium/capital deployed.

## Features

- Track options trades (calls and puts)
- Monitor current prices via Tradier API
- Calculate realized and unrealized P&L
- Track capital deployed for cash-secured puts (strike x 100 x quantity)
- Price history charts
- Scheduled price fetching (3x daily during market hours)
- Dashboard with summary statistics

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Tradier API key (free sandbox key available at [developer.tradier.com](https://developer.tradier.com))

### Setup

1. Clone or copy the project to your machine

2. Create a `.env` file from the example:
   ```bash
   cp .env.example .env
   ```

3. Edit `.env` and add your Tradier API key:
   ```
   TRADIER_API_KEY=your_api_key_here
   TRADIER_SANDBOX=true
   ```

4. Build and run with Docker Compose:
   ```bash
   docker-compose up --build
   ```

5. Access the web UI at `http://localhost:8080`

## Configuration

| Environment Variable | Description | Default |
|---------------------|-------------|---------|
| `TRADIER_API_KEY` | Your Tradier API token | (required) |
| `TRADIER_SANDBOX` | Use sandbox API (free, 15-min delayed) | `true` |
| `DATABASE_PATH` | Path to SQLite database file | `/app/data/options.db` |

## API Endpoints

### Trades
- `GET /api/trades` - List all trades (filters: `status`, `ticker`)
- `GET /api/trades/{id}` - Get single trade with current price
- `POST /api/trades` - Create new trade
- `PUT /api/trades/{id}` - Update trade
- `POST /api/trades/{id}/close` - Close a trade
- `DELETE /api/trades/{id}` - Delete trade

### Prices
- `GET /api/prices/{trade_id}` - Get price history for a trade
- `POST /api/prices/refresh` - Manually trigger price refresh

### Dashboard
- `GET /api/trades/dashboard` - Get summary statistics

## Trade Actions

- **Sell to Open (STO)**: Open a short position (e.g., selling a CSP)
- **Buy to Open (BTO)**: Open a long position
- **Sell to Close (STC)**: Close a long position
- **Buy to Close (BTC)**: Close a short position

## Scheduled Price Fetching

Prices are automatically fetched 3 times daily during market hours (Eastern Time):
- 10:00 AM - After market open settles
- 12:30 PM - Midday check
- 3:30 PM - Before market close

Only weekdays (Monday-Friday) are scheduled.

## Data Storage

- SQLite database stored in `./data/options.db`
- Database file is mounted as a Docker volume for persistence
- Back up the `data/` directory to preserve your trades

## Development

### Running without Docker

1. Create a Python virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Set environment variables:
   ```bash
   export TRADIER_API_KEY=your_key
   export DATABASE_PATH=./data/options.db
   ```

4. Run the application:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```

### Project Structure

```
options-tracker/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example
├── app/
│   ├── main.py              # FastAPI app entry point
│   ├── config.py            # Settings/env vars
│   ├── database.py          # SQLite connection + init
│   ├── models.py            # Pydantic models
│   ├── routers/
│   │   ├── trades.py        # CRUD for trades
│   │   └── prices.py        # Price history endpoints
│   ├── services/
│   │   ├── tradier.py       # Tradier API client
│   │   └── scheduler.py     # Price fetch scheduler
│   └── static/
│       ├── index.html       # Main SPA
│       ├── css/style.css
│       └── js/app.js
└── data/                    # SQLite DB (volume mount)
```

## Tradier API

### Sandbox vs Production

- **Sandbox** (`TRADIER_SANDBOX=true`): Free, 15-minute delayed quotes
- **Production** (`TRADIER_SANDBOX=false`): Real-time quotes, requires funded brokerage account

Get your API key at [developer.tradier.com](https://developer.tradier.com)

## License

MIT
