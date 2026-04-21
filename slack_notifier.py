"""
Slack Notifier for Weekend Getaway Flight Deals

Monitors deals.json for price drops below configured thresholds and sends
Slack alerts. Tracks alert history to prevent notification spam.

Usage:
    python slack_notifier.py [--once]

Options:
    --once: Run a single check and exit (useful for testing/cron)
    
Configuration:
    Set thresholds and Slack webhook in config/thresholds.json
"""

import json
import sqlite3
import time
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
import requests


class SlackNotifier:
    def __init__(
        self,
        deals_file: str = "deals.json",
        config_file: str = "config/thresholds.json",
        db_file: str = "alerts.db",
        check_interval: int = 1800  # 30 minutes
    ):
        self.deals_file = Path(deals_file)
        self.config_file = Path(config_file)
        self.db_file = Path(db_file)
        self.check_interval = check_interval
        
        self._init_database()
        self._load_config()
    
    def _init_database(self):
        """Create alerts tracking database if it doesn't exist."""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                destination TEXT NOT NULL,
                price REAL NOT NULL,
                date TEXT NOT NULL,
                alert_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                deal_hash TEXT UNIQUE NOT NULL
            )
        """)
        
        conn.commit()
        conn.close()
    
    def _load_config(self):
        """Load threshold configuration and Slack webhook URL."""
        if not self.config_file.exists():
            self._create_default_config()
        
        with open(self.config_file, 'r') as f:
            config = json.load(f)
        
        self.webhook_url = config.get('slack_webhook_url')
        self.thresholds = config.get('thresholds', {})
        self.alert_cooldown_hours = config.get('alert_cooldown_hours', 24)
        
        if not self.webhook_url:
            print("WARNING: No Slack webhook URL configured in thresholds.json")
    
    def _create_default_config(self):
        """Create default configuration file with examples."""
        default_config = {
            "slack_webhook_url": "https://hooks.slack.com/services/YOUR/WEBHOOK/URL",
            "alert_cooldown_hours": 24,
            "thresholds": {
                "Barcelona": 100.0,
                "Paris": 120.0,
                "Amsterdam": 110.0,
                "Berlin": 90.0,
                "Rome": 130.0,
                "Prague": 85.0,
                "Budapest": 80.0,
                "Krakow": 75.0,
                "Vienna": 95.0,
                "Copenhagen": 140.0
            }
        }
        
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_file, 'w') as f:
            json.dump(default_config, f, indent=2)
        
        print(f"Created default config at {self.config_file}")
        print("Please update with your Slack webhook URL and desired price thresholds")
    
    def _load_deals(self) -> List[Dict]:
        """Load current deals from deals.json."""
        if not self.deals_file.exists():
            return []
        
        with open(self.deals_file, 'r') as f:
            return json.load(f)
    
    def _create_deal_hash(self, deal: Dict) -> str:
        """Create unique identifier for a deal to track alerts."""
        # Hash based on destination, price, and departure