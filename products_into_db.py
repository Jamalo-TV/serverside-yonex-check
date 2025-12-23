from __future__ import annotations
import re
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
import argparse
import certifi  # Up-to-date CA bundle
import requests
from bs4 import BeautifulSoup
import sqlite3
from pathlib import Path
import sys
import pandas as pd
from datetime import datetime
import json

BASE_URL = "https://www.yonex.ch"

# Database and CSV directories - organized in specific folders
DB_DIR = Path(__file__).parent / 'yonex_data' / 'databases'
CSV_DIR = Path(__file__).parent / 'yonex_data' / 'csv_exports'

@dataclass
class Product:
    name: str
    pic_url: str
    description: str
    price: str
    sizes: str  # JSON string containing size analysis

class Product_into_db:
    @staticmethod
    def init_db(db_path: Path) -> None:
        """Initializes the SQLite database and creates the products table if it doesn't exist."""
        # Create db directory if it doesn't exist
        db_path.parent.mkdir(parents=True, exist_ok=True)
        
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    pic_url TEXT,
                    description TEXT,
                    price TEXT,
                    sizes TEXT,
                    type TEXT,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Create index for better search performance
            conn.execute("CREATE INDEX IF NOT EXISTS idx_products_name ON products(name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_products_type ON products(type)")
            conn.commit()

    @staticmethod
    def rebuild_database(products: List[Dict[str, Any]], db_path: Path) -> int:
        """Completely rebuild the database with fresh data in sequential order. Returns number of inserted records."""
        Product_into_db.init_db(db_path)
        
        with sqlite3.connect(db_path) as conn:
            # Clear all existing data
            conn.execute("DELETE FROM products")
            # Reset the auto-increment counter
            conn.execute("DELETE FROM sqlite_sequence WHERE name='products'")
            print(f"🗑️ Cleared existing data from {db_path}")
            
            # Insert all products with sequential IDs starting from 1
            inserted = 0
            for index, product in enumerate(products, start=1):
                conn.execute("""
                    INSERT INTO products (id, name, pic_url, description, price, sizes, type, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    index,  # Sequential ID starting from 1
                    product['name'],
                    product['pic_url'],
                    product['description'],
                    product['price'],
                    product.get('sizes', ''),
                    product['type'],
                    datetime.now().isoformat()
                ))
                inserted += 1
            
            conn.commit()
        
        print(f"✅ Rebuilt database with {inserted} products in sequential order (IDs 1-{inserted})")
        return inserted

    @staticmethod
    def export_to_csv(products: list, category_name: str) -> None:
        """Export products to CSV with size information."""
        import csv
        
        csv_dir = DB_DIR.parent / 'csv_exports'
        csv_dir.mkdir(parents=True, exist_ok=True)
        csv_path = csv_dir / f"{category_name}_products.csv"
        
        try:
            with open(csv_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                
                # Header with sizes columns
                writer.writerow(['name', 'pic_url', 'description', 'price', 'sizes_available', 'sizes_sold_out', 'sizes_analysis_status'])
                
                # Data rows
                for product in products:
                    # Parse sizes JSON
                    try:
                        sizes_data = json.loads(product.sizes)
                        available = ', '.join(sizes_data.get('available', []))
                        sold_out = ', '.join(sizes_data.get('sold_out', []))
                        analysis = 'Success' if not sizes_data.get('error') else f"Error: {sizes_data.get('error')}"
                    except:
                        available = ''
                        sold_out = ''
                        analysis = 'Parse Error'
                    
                    writer.writerow([
                        product.name,
                        product.pic_url,
                        product.description,
                        product.price,
                        available,
                        sold_out,
                        analysis
                    ])
            
            print(f"📊 CSV exported to {csv_path}")
            
        except Exception as e:
            print(f"❌ Error exporting CSV: {e}")

    @staticmethod
    def store_products_from_dict(products_dict: dict, category_name: str) -> None:
        """Store products from a dictionary (already scraped data) without re-scraping or re-analyzing."""
        try:
            if not products_dict:
                print("⚠️ No products to store")
                return
            
            # Create database path
            db_path = DB_DIR / f"{category_name}_products.db"
            
            # Create database
            Product_into_db.init_db(db_path)
            
            # Store products
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                
                # Clear existing data for this category
                cursor.execute("DELETE FROM products WHERE type = ?", (category_name,))
                
                # Insert products from dictionary
                for image_url, product_data in products_dict.items():
                    description = product_data.get('original_description', product_data.get('description', ''))
                    
                    # Check if sizes already analyzed (passed from caller), otherwise skip Gemini
                    # This avoids redundant API calls when data already has size info
                    if 'sizes' in product_data and product_data['sizes']:
                        sizes_json = product_data['sizes'] if isinstance(product_data['sizes'], str) else json.dumps(product_data['sizes'])
                    else:
                        # Only call Gemini if sizes not already provided
                        # For bulk storage, we skip Gemini to avoid rate limits - sizes can be added later
                        sizes_json = json.dumps({"available": [], "sold_out": [], "note": "Size analysis pending"})
                    
                    cursor.execute('''
                        INSERT INTO products (name, pic_url, description, price, sizes, type)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (
                        product_data.get('name', ''),
                        image_url,
                        description,
                        product_data.get('price', ''),
                        sizes_json,
                        category_name
                    ))
                
                conn.commit()
                print(f"📦 Stored {len(products_dict)} products in {db_path}")
            
        except Exception as e:
            print(f"❌ Error in store_products_from_dict: {e}")

    @staticmethod
    def scrape_and_store(url: str, category_name: str, insecure: bool = False) -> None:
        """Scrape products from URL and store in category-specific database with size analysis."""
        try:
            print(f"🔗 Scraping products from {url}...")
            
            # Get products with size analysis
            products = Product_into_db.get_products(url, insecure=insecure)
            
            if not products:
                print("⚠️ No products found to store")
                return
            
            # Create database path
            db_path = DB_DIR / f"{category_name}_products.db"
            
            # Create database
            Product_into_db.init_db(db_path)
            
            # Store products
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                
                # Clear existing data for this category
                cursor.execute("DELETE FROM products WHERE type = ?", (category_name,))
                
                # Insert new products
                for product in products:
                    cursor.execute('''
                        INSERT INTO products (name, pic_url, description, price, sizes, type)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (
                        product.name,
                        product.pic_url,
                        product.description,
                        product.price,
                        product.sizes,
                        category_name
                    ))
                
                conn.commit()
                print(f"📦 Stored {len(products)} products in {db_path}")
            
            # Also create CSV export with size information
            Product_into_db.export_to_csv(products, category_name)
            
        except Exception as e:
            print(f"❌ Error in scrape_and_store: {e}")

    @staticmethod
    def get_products(url: str, insecure: bool = False) -> list:
        """Get products from the URL and return as list of Product objects."""
        products = []
        
        try:
            if insecure:
                response = requests.get(url, verify=False, timeout=10)
            else:
                response = requests.get(url, timeout=10)
            
            response.raise_for_status()
            
        except requests.RequestException as e:
            print(f"❌ Error fetching {url}: {e}")
            return products
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extract only the product content area
        main_content = soup.find('div', id='content3')
        if not main_content:
            print("⚠️ Could not find main content area")
            return products
        
        headers = main_content.find_all('h2', class_='underline')
        print(f"🔍 Found {len(headers)} products on the page")
        
        for header in headers:
            try:
                # Get product name
                header_copy = header.__copy__()
                name_element = header_copy.find('a')
                if name_element:
                    name_element.extract()
                name = header_copy.get_text(strip=True)
                
                if not name:
                    continue
                
                # Get image URL
                image_div = header.find_next_sibling('div', class_='image')
                pic_url = ''
                if image_div:
                    link = image_div.find('a')
                    if link and link.has_attr('href'):
                        pic_url = link['href']
                
                # Get description (original text for Gemini)
                description_div = header.find_next_sibling('div', class_='description')
                description = ''
                if description_div:
                    description = description_div.get_text(separator=' ', strip=True)
                
                # Get price
                attributes_dl = header.find_next_sibling('dl', class_='attributes')
                price = ''
                if attributes_dl:
                    price_dt = attributes_dl.find('dt', string=lambda text: text and 'Preis' in text)
                    if price_dt:
                        price_dd = price_dt.find_next_sibling('dd')
                        if price_dd:
                            price = price_dd.get_text(strip=True)
                
                # Sizes will be analyzed by yonex_site_checker.py, not here
                sizes_json = json.dumps({"available": [], "sold_out": [], "note": "Size analysis pending"})
                
                # Create product object
                product = Product(
                    name=name,
                    pic_url=pic_url,
                    description=description,
                    price=price,
                    sizes=sizes_json
                )
                
                products.append(product)
                
            except Exception as e:
                print(f"⚠️ Error processing product: {e}")
                continue
        
        print(f"📦 Successfully processed {len(products)} products with size analysis")
        return products

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape a single Yonex category and store products in a database.")
    parser.add_argument('--url', required=True, help='URL to scrape')
    parser.add_argument('--category', required=True, help='Category name for database file')
    parser.add_argument('--insecure', action='store_true', 
                    help='Disable SSL certificate verification (USE ONLY IF YOU UNDERSTAND THE RISKS).')
    
    args = parser.parse_args()
    
    Product_into_db.scrape_and_store(args.url, args.category, args.insecure)