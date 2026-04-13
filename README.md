# Weekend Getaway ✈️

**Automated flight deal discovery and notifications for spontaneous European adventures**

Weekend Getaway monitors flight prices from your home airport and alerts you when cheap weekend trips appear. Perfect for travelers who want to explore Europe without spending hours browsing flight aggregators.

---

## ⚡ Quick Start (5 minutes)

```bash
# 1. Clone and install
git clone https://github.com/yourusername/weekend-getaway.git
cd weekend-getaway
pip install -r requirements.txt

# 2. Configure your API keys (see Setup below)
cp .env.example .env
# Edit .env with your API credentials

# 3. Run the scanner
python scanner.py

# 4. View deals in the dashboard
cd dashboard
python app.py
# Visit http://localhost:8050
```

---

## 🎯 What It Does

- **Scans flights** from your home airport to European destinations
- **Finds weekend trips** (Fri-Sun or Sat-Mon departures)
- **Enriches deals** with weather forecasts and destination info
- **Sends notifications** via email, Telegram, or Discord when deals appear
- **Dashboard** to browse all discovered deals with filters and sorting

---

## 🏗️ Architecture

```
┌─────────────┐
│  scanner.py │  ← Queries flight APIs for weekend departures
└──────┬──────┘
       │
       ▼
┌──────────────────┐
│ enrichments.py   │  ← Adds weather, city info, attractions
└──────┬───────────┘
       │
       ▼
┌──────────────┐         ┌─────────────┐
│ notifier.py  │────────>│  dashboard/ │
└──────────────┘         └─────────────┘
 Sends alerts            Plotly Dash UI
 (Email/Telegram)        for browsing deals
```

**Data Flow:**
1. Scanner fetches flight data from API (e.g., Skyscanner, Amadeus)
2. Enrichment adds context (weather, attractions, city descriptions)
3. Notifier sends alerts for deals under your price threshold
4. Dashboard displays all deals in an interactive web interface

---

## 🚀 Setup Instructions

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

**Key libraries:**
- `requests` - API calls
- `python-dotenv` - Environment variable management
- `plotly` + `dash` - Interactive dashboard
- Email/notification clients (check `requirements.txt` for specifics)

### 2. Configure Environment Variables

Create a `.env` file in the project root:

```bash
# Flight Data API (choose one)
SKYSCANNER_API_KEY=your_key_here
# OR
AMADEUS_API_KEY=your_key_here
AMADEUS_API_SECRET=your_secret_here

# Home Airport (IATA code)
HOME_AIRPORT=DUB  # Example: Dublin

# Weather API (optional but recommended)
OPENWEATHER_API_KEY=your_key_here

# Notification Channels (choose one or more)
SENDGRID_API_KEY=your_key_here  # For email
TELEGRAM_BOT_TOKEN=your_token_here
TELEGRAM_CHAT_ID=your_chat_id
DISCORD_WEBHOOK_URL=your_webhook_url

# Price Threshold (optional)
MAX_PRICE=100  # Only notify for deals under €100
```

**Getting API Keys:**
- **Flight data:** Sign up for [RapidAPI Skyscanner](https://rapidapi.com/skyscanner/api/skyscanner-flight-search) or [Amadeus for Developers](https://developers.amadeus.com/)
- **Weather:** Free tier at [OpenWeatherMap](https://openweathermap.org/api)
- **Email:** [SendGrid](https://sendgrid.com/) free tier (100 emails/day)
- **Telegram:** Create a bot via [@BotFather](https://t.me/botfather)
- **Discord:** Create a webhook in your server settings

### 3. Run the