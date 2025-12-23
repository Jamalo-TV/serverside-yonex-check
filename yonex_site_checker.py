#gabrielserver

import requests
import time
import re
import json
import argparse
from pathlib import Path
from bs4 import BeautifulSoup
from products_into_db import Product_into_db
from datetime import datetime
from talk_to_ai import analyze_sizes, set_ai_provider, get_current_config
import os
from telegram_notifier import Notifier
import sqlite3

# Define sites to monitor
SITES = [
    {"name": "bekleidung", "url": "https://www.yonex.ch/de/badminton/produkte/bekleidung/"},
    #{"name": "saiten", "url": "https://www.yonex.ch/de/badminton/produkte/saiten/"},
    {"name": "schuhe", "url": "https://www.yonex.ch/de/badminton/produkte/schuhe/"},
    #{"name": "taschen", "url": "https://www.yonex.ch/de/badminton/produkte/taschen/"},
    #{"name": "accessoires-etc", "url": "https://www.yonex.ch/de/badminton/produkte/accessoires-etc/"}
]

# Directory to store data files
HISTORY_DIR = Path(__file__).parent / 'yonex_data' / 'history'
DB_DIR = Path(__file__).parent / 'yonex_data' / 'databases'

# Initialize notifier
notifier = Notifier()

def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Yonex Product Monitoring with AI Size Analysis")
    parser.add_argument(
        "-redo", "--redo",
        action="store_true",
        help="Rebuild the entire database from scratch (deletes existing data)"
    )
    parser.add_argument(
        "-gemini2", "--gemini2",
        action="store_true",
        help="Use Gemini 2.0 Flash model"
    )
    parser.add_argument(
        "-gemini3", "--gemini3",
        action="store_true",
        help="Use Gemini 3 Flash Preview model"
    )
    parser.add_argument(
        "-qwen", "--qwen",
        action="store_true",
        help="Use Qwen model instead of Gemini"
    )
    return parser.parse_args()

def setup_ai_provider(args):
    """Set the AI provider based on command line arguments."""
    if args.qwen:
        set_ai_provider("qwen")
    elif args.gemini2:
        set_ai_provider("gemini", "2")
    elif args.gemini3:
        set_ai_provider("gemini", "3")
    else:
        set_ai_provider("gemini", "2.5")  # Default

def redo_database():
    """Delete all databases and history files to start fresh."""
    print("\n🔄 REDO MODE: Rebuilding entire database from scratch...")
    print("=" * 60)
    
    # Delete database files
    if DB_DIR.exists():
        for db_file in DB_DIR.glob("*.db"):
            print(f"🗑️  Deleting database: {db_file.name}")
            db_file.unlink()
    
    # Delete history JSON files
    if HISTORY_DIR.exists():
        for json_file in HISTORY_DIR.glob("*.json"):
            print(f"🗑️  Deleting history: {json_file.name}")
            json_file.unlink()
    
    print("✅ All databases and history files deleted")
    print("📦 Will rebuild on next check...")
    print("=" * 60 + "\n")

def add_sizes_column_to_database(db_path: Path):
    """Add sizes column to existing database if it doesn't exist."""
    if not db_path.exists():
        return
    
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            
            # Check if sizes column exists
            cursor.execute("PRAGMA table_info(products)")
            columns = [row[1] for row in cursor.fetchall()]
            
            if 'sizes' not in columns:
                print(f"📊 Adding 'sizes' column to {db_path}")
                cursor.execute("ALTER TABLE products ADD COLUMN sizes TEXT")
                conn.commit()
                print(f"✅ Added 'sizes' column to database")
            else:
                print(f"✅ 'sizes' column already exists in {db_path}")
                
    except Exception as e:
        print(f"❌ Error adding sizes column: {e}")

def update_sizes_for_existing_products(db_path: Path, category_name: str):
    """Update sizes column for existing products by analyzing their descriptions."""
    if not db_path.exists():
        return
    
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            
            # Get products without size analysis
            cursor.execute("""
                SELECT id, name, description 
                FROM products 
                WHERE type = ? AND (sizes IS NULL OR sizes = '')
            """, (category_name,))
            
            products_to_update = cursor.fetchall()
            
            if not products_to_update:
                print(f"✅ All products in {category_name} already have size analysis")
                return
            
            print(f"🧠 Analyzing sizes for {len(products_to_update)} existing products...")
            
            for product_id, name, description in products_to_update:
                if description:
                    print(f"   Analyzing: {name[:50]}...")
                    size_analysis = analyze_sizes(description)
                    sizes_json = json.dumps(size_analysis)
                    
                    cursor.execute("""
                        UPDATE products 
                        SET sizes = ? 
                        WHERE id = ?
                    """, (sizes_json, product_id))
            
            conn.commit()
            print(f"✅ Updated size analysis for {len(products_to_update)} products")
            
    except Exception as e:
        print(f"❌ Error updating sizes: {e}")

def analyze_size_changes(old_description: str, new_description: str) -> dict:
    """Analyze size changes between old and new product descriptions."""
    print("🔍 Analyzing size changes with AI...")
    
    old_sizes = analyze_sizes(old_description)
    new_sizes = analyze_sizes(new_description)
    
    # Compare available sizes
    old_available = set(old_sizes.get("available", []))
    new_available = set(new_sizes.get("available", []))
    
    # Compare sold out sizes
    old_sold_out = set(old_sizes.get("sold_out", []))
    new_sold_out = set(new_sizes.get("sold_out", []))
    
    # Calculate changes
    newly_available = new_available - old_available
    newly_sold_out = new_sold_out - old_sold_out
    no_longer_available = old_available - new_available
    no_longer_sold_out = old_sold_out - new_sold_out
    
    return {
        "old_sizes": old_sizes,
        "new_sizes": new_sizes,
        "newly_available": list(newly_available),
        "newly_sold_out": list(newly_sold_out),
        "no_longer_available": list(no_longer_available),
        "no_longer_sold_out": list(no_longer_sold_out),
        "has_size_changes": bool(newly_available or newly_sold_out or no_longer_available or no_longer_sold_out)
    }

def normalize_text(text: str) -> str:
    """Normalize text to remove inconsistencies that don't affect content."""
    if not text:
        return ""
    
    # Convert to lowercase for consistent comparison
    text = text.lower()
    
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text)
    
    # Remove trailing/leading whitespace
    text = text.strip()
    
    # Remove common dynamic elements
    text = re.sub(r'timestamp[^"]*"[^"]*"', '', text)
    text = re.sub(r'session[^"]*"[^"]*"', '', text)
    text = re.sub(r'csrf[^"]*"[^"]*"', '', text)
    text = re.sub(r'nonce[^"]*"[^"]*"', '', text)
    text = re.sub(r'_token[^"]*"[^"]*"', '', text)
    
    # Remove any remaining quotes and normalize
    text = re.sub(r'["\']', '', text)
    
    return text

def normalize_image_url(url: str) -> str:
    """Normalize image URL to create a stable unique identifier."""
    if not url:
        return ""
    
    # Remove protocol and domain for consistency
    if url.startswith('https://www.yonex.ch'):
        url = url.replace('https://www.yonex.ch', '')
    elif url.startswith('http://www.yonex.ch'):
        url = url.replace('http://www.yonex.ch', '')
    elif url.startswith('//www.yonex.ch'):
        url = url.replace('//www.yonex.ch', '')
    
    # Ensure it starts with /
    if not url.startswith('/'):
        url = '/' + url
    
    return url.lower().strip()

def get_products_with_image_ids(url: str) -> dict:
    """Extract products using image URL as unique identifier."""
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Extract only the product content area
    main_content = soup.find('div', id='content3')
    if not main_content:
        print("⚠️ Could not find main content area")
        return {}
    
    # Extract products with image URL as key
    products_by_image = {}
    
    headers = main_content.find_all('h2', class_='underline')
    print(f"🔍 Found {len(headers)} products on the page")
    
    for header in headers:
        try:
            # Get product name
            header_copy = header.__copy__()
            name_element = header_copy.find('a')
            if name_element:
                name_element.extract()
            name = normalize_text(header_copy.get_text(strip=True))
            
            if not name:
                continue
            
            # Get image URL as unique identifier
            image_div = header.find_next_sibling('div', class_='image')
            pic_url = ''
            if image_div:
                link = image_div.find('a')
                if link and link.has_attr('href'):
                    pic_url = normalize_image_url(link['href'])
            
            if not pic_url:
                print(f"⚠️ No image URL found for product: {name}")
                continue
            
            # Get description (keep original for Gemini analysis)
            description_div = header.find_next_sibling('div', class_='description')
            original_description = ''
            if description_div:
                original_description = description_div.get_text(separator=' ', strip=True)
            
            # Normalize description for comparison
            normalized_description = normalize_text(original_description)
            
            # Get price
            attributes_dl = header.find_next_sibling('dl', class_='attributes')
            price = ''
            if attributes_dl:
                price_dt = attributes_dl.find('dt', string=lambda text: text and 'Preis' in text)
                if price_dt:
                    price_dd = price_dt.find_next_sibling('dd')
                    if price_dd:
                        price = price_dd.get_text(strip=True)
            price = normalize_text(price)
            
            # Store product with image URL as key
            products_by_image[pic_url] = {
                'name': name,
                'image_url': pic_url,
                'description': normalized_description,
                'original_description': original_description,  # Keep original for Gemini
                'price': price,
                'sizes': None  # Will be analyzed only if needed (on changes)
            }
            
        except Exception as e:
            print(f"⚠️ Error processing product: {e}")
            continue
    
    print(f"📝 Processed {len(products_by_image)} products with unique image URLs")
    return products_by_image

def analyze_product_changes(url: str, site_name: str) -> dict:
    """Analyze product changes using image URLs as unique identifiers."""
    try:
        # Get current products
        current_products = get_products_with_image_ids(url)
        
        # Load previous products
        history_file = HISTORY_DIR / f"{site_name}_products.json"
        previous_products = {}
        
        if history_file.exists():
            with open(history_file, 'r', encoding='utf-8') as f:
                previous_products = json.load(f)
        
        # Analyze changes using image URLs as identifiers
        current_images = set(current_products.keys())
        previous_images = set(previous_products.keys())
        
        removed_images = previous_images - current_images
        added_images = current_images - previous_images
        common_images = current_images & previous_images
        
        # Check for content changes in existing products
        modified_products = []
        for image_url in common_images:
            current = current_products[image_url]
            previous = previous_products[image_url]
            
            # Compare all fields except image_url
            if (current['name'] != previous['name'] or 
                current['description'] != previous['description'] or 
                current['price'] != previous['price']):
                
                modification = {
                    'image_url': image_url,
                    'old': previous,
                    'new': current
                }
                  # If description changed, analyze size changes with Gemini
                if current['description'] != previous['description']:
                    print(f"🧠 Description changed for {current['name']}, analyzing sizes...")
                    
                    old_desc = previous.get('original_description', previous['description'])
                    new_desc = current.get('original_description', current['description'])
                    
                    size_analysis = analyze_size_changes(old_desc, new_desc)
                    modification['size_analysis'] = size_analysis
                    
                    # Store the new sizes in current_products for database
                    current_products[image_url]['sizes'] = json.dumps(size_analysis.get('new_sizes', {}))
                
                modified_products.append(modification)
        
        # Analyze sizes for ADDED products only (they need initial size analysis)
        added_products_list = []
        for img in added_images:
            product = current_products[img].copy()
            # Only analyze if we have a description
            if product.get('original_description'):
                print(f"🧠 Analyzing sizes for new product: {product['name'][:40]}...")
                size_analysis = analyze_sizes(product.get('original_description', ''))
                product['sizes'] = json.dumps(size_analysis)
            else:
                product['sizes'] = json.dumps({"available": [], "sold_out": []})
            added_products_list.append(product)
            # Also update in current_products for database storage
            current_products[img]['sizes'] = product['sizes']
        
        changes = {
            'removed': [previous_products[img] for img in removed_images],
            'added': added_products_list,
            'modified': modified_products,
            'total_current': len(current_products),
            'total_previous': len(previous_products),
            'current_products': current_products  # Include for database update
        }
        
        # Save current state for next comparison
        history_file.parent.mkdir(parents=True, exist_ok=True)
        with open(history_file, 'w', encoding='utf-8') as f:
            json.dump(current_products, f, indent=2, ensure_ascii=False)
        
        return changes
        
    except Exception as e:
        error_msg = f"Error analyzing changes for {site_name}: {e}"
        print(f"❌ {error_msg}")
        notifier.send_error_to_owner(error_msg)
        return {'removed': [], 'added': [], 'modified': [], 'total_current': 0, 'total_previous': 0, 'current_products': {}}

def send_notifications_for_changes(changes: dict, site_name: str):
    """Send notifications for all detected changes with full detailed text (same as log file)."""
    
    if not any([changes['removed'], changes['added'], changes['modified']]):
        return
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Build the full message exactly like the log file format
    lines = []
    lines.append(f"{'='*50}")
    lines.append(f"PRODUCT CHANGES DETECTED - {site_name.upper()}")
    lines.append(f"Timestamp: {timestamp}")
    lines.append(f"Total Products: {changes['total_previous']} → {changes['total_current']}")
    lines.append(f"{'='*50}")
    
    # Removed products
    if changes['removed']:
        lines.append(f"\n🗑️  REMOVED PRODUCTS ({len(changes['removed'])}):")
        lines.append("-" * 40)
        for i, product in enumerate(changes['removed'], 1):
            lines.append(f"REMOVED #{i}:")
            lines.append(f"  Name: {product['name']}")
            lines.append(f"  Price: {product['price']}")
            lines.append(f"  Image URL: {product['image_url']}")
            lines.append(f"  Description: {product['description']}")
            lines.append("-" * 30)
    
    # Added products
    if changes['added']:
        lines.append(f"\n🆕 NEW PRODUCTS ({len(changes['added'])}):")
        lines.append("-" * 40)
        for i, product in enumerate(changes['added'], 1):
            lines.append(f"NEW #{i}:")
            lines.append(f"  Name: {product['name']}")
            lines.append(f"  Price: {product['price']}")
            lines.append(f"  Image URL: {product['image_url']}")
            lines.append(f"  Description: {product['description']}")
            lines.append("-" * 30)
    
    # Modified products with detailed comparison
    if changes['modified']:
        lines.append(f"\n🔄 MODIFIED PRODUCTS ({len(changes['modified'])}):")
        lines.append("-" * 40)
        for i, mod in enumerate(changes['modified'], 1):
            lines.append(f"MODIFIED #{i} - Image: {mod['image_url']}")
            
            old = mod['old']
            new = mod['new']
            
            # Name comparison
            if old['name'] != new['name']:
                lines.append(f"  ❌ NAME CHANGED:")
                lines.append(f"     OLD: {old['name']}")
                lines.append(f"     NEW: {new['name']}")
            else:
                lines.append(f"  ✅ Name (unchanged): {new['name']}")
            
            # Price comparison
            if old['price'] != new['price']:
                lines.append(f"  ❌ PRICE CHANGED:")
                lines.append(f"     OLD: {old['price']}")
                lines.append(f"     NEW: {new['price']}")
            else:
                lines.append(f"  ✅ Price (unchanged): {new['price']}")
            
            # Description comparison
            if old['description'] != new['description']:
                lines.append(f"  ❌ DESCRIPTION CHANGED:")
                lines.append(f"     OLD: {old['description']}")
                lines.append(f"     NEW: {new['description']}")
            else:
                lines.append(f"  ✅ Description (unchanged): {new['description'][:100]}...")
            
            # Size analysis if available
            if 'size_analysis' in mod:
                size_analysis = mod['size_analysis']
                if size_analysis['has_size_changes']:
                    lines.append(f"  🧠 SIZE CHANGES DETECTED:")
                    
                    if size_analysis['newly_available']:
                        lines.append(f"     ✅ Newly Available: {size_analysis['newly_available']}")
                    
                    if size_analysis['newly_sold_out']:
                        lines.append(f"     ❌ Newly Sold Out: {size_analysis['newly_sold_out']}")
                    
                    if size_analysis['no_longer_available']:
                        lines.append(f"     ⚠️ No Longer Available: {size_analysis['no_longer_available']}")
                    
                    if size_analysis['no_longer_sold_out']:
                        lines.append(f"     🔄 No Longer Sold Out: {size_analysis['no_longer_sold_out']}")
                    
                    lines.append(f"     OLD Sizes - Available: {size_analysis['old_sizes'].get('available', [])}")
                    lines.append(f"     OLD Sizes - Sold Out: {size_analysis['old_sizes'].get('sold_out', [])}")
                    lines.append(f"     NEW Sizes - Available: {size_analysis['new_sizes'].get('available', [])}")
                    lines.append(f"     NEW Sizes - Sold Out: {size_analysis['new_sizes'].get('sold_out', [])}")
                else:
                    lines.append(f"  ✅ Size availability unchanged")
            
            lines.append(f"  ✅ Image URL: {new['image_url']}")
            lines.append("-" * 30)
    
    lines.append(f"\nEnd of changes for {site_name} at {timestamp}")
    lines.append(f"{'='*50}")
    
    # Join all lines and send as a single notification
    full_message = "\n".join(lines)
    
    # Send the full detailed message to Telegram
    notifier.send_notification(
        title=f"🔔 Yonex {site_name.upper()} Update",
        body=full_message,
        tag=f"changes-{site_name}"
    )
    
    print(f"📱 Sent full detailed notification for {site_name}")

def display_product_warnings(changes: dict, site_name: str):
    """Display detailed warnings for removed and added products."""
    
    # Warning for removed products
    if changes['removed']:
        print(f"\n🚨 WARNING: {len(changes['removed'])} PRODUCT(S) REMOVED FROM {site_name.upper()}!")
        print("=" * 60)
        for i, product in enumerate(changes['removed'], 1):
            print(f"🗑️  REMOVED PRODUCT #{i}:")
            print(f"   Name: {product['name']}")
            print(f"   Price: {product['price']}")
            print(f"   Image: {product['image_url']}")
            print(f"   Description: {product['description'][:80]}...")
            print("-" * 40)
    
    # Warning for added products
    if changes['added']:
        print(f"\n🚨 WARNING: {len(changes['added'])} NEW PRODUCT(S) ADDED TO {site_name.upper()}!")
        print("=" * 60)
        for i, product in enumerate(changes['added'], 1):
            print(f"🆕 NEW PRODUCT #{i}:")
            print(f"   Name: {product['name']}")
            print(f"   Price: {product['price']}")
            print(f"   Image: {product['image_url']}")
            print(f"   Description: {product['description'][:80]}...")
            print("-" * 40)
    
    # Info for modified products with detailed comparison and size changes
    if changes['modified']:
        print(f"\n📝 INFO: {len(changes['modified'])} PRODUCT(S) MODIFIED ON {site_name.upper()}:")
        print("=" * 60)
        for i, mod in enumerate(changes['modified'], 1):
            print(f"🔄 MODIFIED PRODUCT #{i}:")
            print(f"   Image: {mod['image_url']}")
            
            # Show what changed
            old = mod['old']
            new = mod['new']
            
            if old['name'] != new['name']:
                print(f"   ❌ NAME: {old['name']} → {new['name']}")
            else:
                print(f"   ✅ Name: {new['name']}")
            
            if old['price'] != new['price']:
                print(f"   ❌ PRICE: {old['price']} → {new['price']}")
            else:
                print(f"   ✅ Price: {new['price']}")
            
            if old['description'] != new['description']:
                print(f"   ❌ DESCRIPTION CHANGED")
                # Show size changes if available
                if 'size_analysis' in mod and mod['size_analysis']['has_size_changes']:
                    size_analysis = mod['size_analysis']
                    print(f"   🧠 SIZE CHANGES:")
                    if size_analysis['newly_available']:
                        print(f"      ✅ Newly Available: {', '.join(size_analysis['newly_available'])}")
                    if size_analysis['newly_sold_out']:
                        print(f"      ❌ Newly Sold Out: {', '.join(size_analysis['newly_sold_out'])}")
            else:
                print(f"   ✅ Description: unchanged")
            
            print("-" * 40)

def log_changes(changes: dict, site_name: str):
    """Log detailed changes to a single master file with complete old vs new info and size analysis."""
    if not any([changes['removed'], changes['added'], changes['modified']]):
        return
    
    log_dir = HISTORY_DIR / 'change_logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Single master log file for all changes
    log_file = log_dir / "yonex_product_changes.txt"
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Append to master log file
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(f"\n{'='*80}\n")
        f.write(f"PRODUCT CHANGES DETECTED - {site_name.upper()}\n")
        f.write(f"Timestamp: {timestamp}\n")
        f.write(f"Total Products: {changes['total_previous']} → {changes['total_current']}\n")
        f.write(f"{'='*80}\n")
        
        # Log removed products
        if changes['removed']:
            f.write(f"\n🗑️  REMOVED PRODUCTS ({len(changes['removed'])}):\n")
            f.write("-" * 60 + "\n")
            for i, product in enumerate(changes['removed'], 1):
                f.write(f"REMOVED #{i}:\n")
                f.write(f"  Name: {product['name']}\n")
                f.write(f"  Price: {product['price']}\n")
                f.write(f"  Image URL: {product['image_url']}\n")
                f.write(f"  Description: {product['description']}\n")
                f.write("-" * 40 + "\n")
        
        # Log added products
        if changes['added']:
            f.write(f"\n🆕 NEW PRODUCTS ({len(changes['added'])}):\n")
            f.write("-" * 60 + "\n")
            for i, product in enumerate(changes['added'], 1):
                f.write(f"NEW #{i}:\n")
                f.write(f"  Name: {product['name']}\n")
                f.write(f"  Price: {product['price']}\n")
                f.write(f"  Image URL: {product['image_url']}\n")
                f.write(f"  Description: {product['description']}\n")
                f.write("-" * 40 + "\n")
        
        # Log modified products with detailed old vs new comparison
        if changes['modified']:
            f.write(f"\n🔄 MODIFIED PRODUCTS ({len(changes['modified'])}):\n")
            f.write("-" * 60 + "\n")
            for i, mod in enumerate(changes['modified'], 1):
                f.write(f"MODIFIED #{i} - Image: {mod['image_url']}\n")
                
                # Compare each field
                old = mod['old']
                new = mod['new']
                
                # Name comparison
                if old['name'] != new['name']:
                    f.write(f"  ❌ NAME CHANGED:\n")
                    f.write(f"     OLD: {old['name']}\n")
                    f.write(f"     NEW: {new['name']}\n")
                else:
                    f.write(f"  ✅ Name (unchanged): {new['name']}\n")
                
                # Price comparison
                if old['price'] != new['price']:
                    f.write(f"  ❌ PRICE CHANGED:\n")
                    f.write(f"     OLD: {old['price']}\n")
                    f.write(f"     NEW: {new['price']}\n")
                else:
                    f.write(f"  ✅ Price (unchanged): {new['price']}\n")
                
                # Description comparison
                if old['description'] != new['description']:
                    f.write(f"  ❌ DESCRIPTION CHANGED:\n")
                    f.write(f"     OLD: {old['description']}\n")
                    f.write(f"     NEW: {new['description']}\n")
                else:
                    f.write(f"  ✅ Description (unchanged): {new['description'][:100]}...\n")
                
                # Size analysis if available
                if 'size_analysis' in mod:
                    size_analysis = mod['size_analysis']
                    if size_analysis['has_size_changes']:
                        f.write(f"  🧠 SIZE CHANGES DETECTED:\n")
                        
                        if size_analysis['newly_available']:
                            f.write(f"     ✅ Newly Available: {size_analysis['newly_available']}\n")
                        
                        if size_analysis['newly_sold_out']:
                            f.write(f"     ❌ Newly Sold Out: {size_analysis['newly_sold_out']}\n")
                        
                        if size_analysis['no_longer_available']:
                            f.write(f"     ⚠️ No Longer Available: {size_analysis['no_longer_available']}\n")
                        
                        if size_analysis['no_longer_sold_out']:
                            f.write(f"     🔄 No Longer Sold Out: {size_analysis['no_longer_sold_out']}\n")
                        
                        f.write(f"     OLD Sizes - Available: {size_analysis['old_sizes'].get('available', [])}\n")
                        f.write(f"     OLD Sizes - Sold Out: {size_analysis['old_sizes'].get('sold_out', [])}\n")
                        f.write(f"     NEW Sizes - Available: {size_analysis['new_sizes'].get('available', [])}\n")
                        f.write(f"     NEW Sizes - Sold Out: {size_analysis['new_sizes'].get('sold_out', [])}\n")
                    else:
                        f.write(f"  ✅ Size availability unchanged\n")
                
                # Image URL (should be the same since it's our key)
                f.write(f"  ✅ Image URL: {new['image_url']}\n")
                f.write("-" * 40 + "\n")
        
        f.write(f"\nEnd of changes for {site_name} at {timestamp}\n")
        f.write(f"{'='*80}\n")
    
    print(f"📝 Changes logged to master file: {log_file}")

def has_site_changed(url: str, site_name: str) -> dict | None:
    """Check if site has changed and provide detailed change analysis.
    Returns the changes dict if changes detected, None otherwise."""
    try:
        print(f"🔍 Checking for changes at {url}...")
        
        # Analyze changes using image URLs
        changes = analyze_product_changes(url, site_name)
        
        has_changes = bool(changes['removed'] or changes['added'] or changes['modified'])
        
        if not has_changes:
            print(f"✅ No product changes detected ({changes['total_current']} products)")
            return None
        
        # Site has changed - show summary first
        print(f"\n🔄 SITE CHANGED: {site_name.upper()}")
        print(f"📊 Product summary: {changes['total_previous']} → {changes['total_current']} products")
        
        # Send notifications for changes
        print(f"\n📱 Sending notifications for changes...")
        send_notifications_for_changes(changes, site_name)
          # Display detailed warnings for critical changes
        display_product_warnings(changes, site_name)
        
        # Log detailed changes to master file
        log_changes(changes, site_name)
        
        print(f"\n✅ Change analysis complete for {site_name}")
        return changes  # Return the changes dict including current_products
        
    except Exception as e:
        error_msg = f"Error checking for changes on {site_name}: {e}"
        print(f"❌ {error_msg}")
        notifier.send_error_to_owner(error_msg)
        return None

if __name__ == "__main__":
    # Parse command line arguments
    args = parse_arguments()
    
    # Set AI provider based on arguments
    setup_ai_provider(args)
    ai_config = get_current_config()
    
    # Create necessary directories
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    DB_DIR.mkdir(parents=True, exist_ok=True)
    
    # Handle redo mode
    if args.redo:
        redo_database()
    
    print("🚀 Starting Yonex product monitoring with AI size analysis...")
    print(f"📁 History directory: {HISTORY_DIR}")
    print(f"📝 Master log file: yonex_product_changes.txt")
    print(f"🤖 AI Provider: {ai_config['provider'].upper()} (Model: {ai_config['model']})")
    print(f"📱 Push notifications: ENABLED")
    print(f"👥 Owner ID: {notifier.owner_id}")
    print(f"👀 Monitoring {len(SITES)} sites for product changes...")
    print("⚠️  Will send notifications for all product changes")
    print("-" * 60)
    
    # Check and update database schema for existing databases
    db_dir = Path(__file__).parent / 'yonex_data' / 'databases'
    if db_dir.exists():
        for site in SITES:
            db_path = db_dir / f"{site['name']}_products.db"
            if db_path.exists():
                add_sizes_column_to_database(db_path)
                update_sizes_for_existing_products(db_path, site['name'])
    
    while True:
        for site in SITES:
            try:
                changes = has_site_changed(site["url"], site["name"])
                if changes:
                    print(f"\n🔔 UPDATING DATABASE FOR {site['name'].upper()}...")
                    print("📦 Storing updated product data (no re-scraping needed)...")
                    
                    # Use the already-scraped products instead of re-scraping
                    Product_into_db.store_products_from_dict(
                        changes.get('current_products', {}), 
                        site["name"]
                    )
                    
                    print(f"✅ Database updated: {site['name']}_products.db")
                    print("=" * 60)
                else:
                    print(f"✓ {site['name']}: No changes detected")
                    
            except Exception as e:
                error_msg = f"Error checking {site['name']} ({site['url']}): {e}"
                print(f"❌ {error_msg}")
                notifier.send_error_to_owner(error_msg)

        print(f"\n⏰ Waiting 20 seconds before next check...")
        print("=" * 60)
        time.sleep(20)