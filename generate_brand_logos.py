import os
import sys
import django

# Setup Django environment
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.products.models import Brand
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import math

# Target directory for media files
MEDIA_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'media')
BRANDS_DIR = os.path.join(MEDIA_ROOT, 'brands')
os.makedirs(BRANDS_DIR, exist_ok=True)

def create_diagonal_gradient(width, height, start_color, end_color):
    """Creates a beautiful smooth linear diagonal gradient from top-left to bottom-right."""
    img = Image.new("RGBA", (width, height))
    for y in range(height):
        for x in range(width):
            # Calculate gradient ratio along the diagonal
            r = (x + y) / (width + height)
            color = tuple(
                int(start_color[i] + (end_color[i] - start_color[i]) * r)
                for i in range(3)
            )
            img.putpixel((x, y), color + (255,))
    return img

def get_font(font_name, size):
    """Utility to load a high-quality Windows system font or fallback gracefully."""
    paths = [
        f"C:\\Windows\\Fonts\\{font_name}.ttf",
        f"C:\\Windows\\Fonts\\arial.ttf",
        f"C:\\Windows\\Fonts\\calibri.ttf",
        f"C:\\Windows\\Fonts\\segoeui.ttf",
    ]
    for path in paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()

def draw_text_centered(draw, text, font, fill, width, height, offset_y=0):
    """Draws text centered horizontally and vertically on the image, with an optional vertical offset."""
    bbox = font.getbbox(text)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    x = (width - text_width) // 2 - bbox[0]
    y = (height - text_height) // 2 - bbox[1] + offset_y
    draw.text((x, y), text, fill=fill, font=font)

def generate_apple(width, height):
    # Minimalist dark premium grey-black gradient
    img = create_diagonal_gradient(width, height, (55, 55, 55), (15, 15, 15))
    draw = ImageDraw.Draw(img)
    
    # Inner border for glassmorphism look
    draw.rounded_rectangle([20, 20, width - 20, height - 20], radius=32, outline=(255, 255, 255, 30), width=3)
    
    # Large Georgia 'A' for Apple
    font_a = get_font("georgiab", 180)
    draw_text_centered(draw, "A", font_a, (255, 255, 255, 240), width, height, offset_y=-30)
    
    # Apple wordmark below
    font_text = get_font("segoeuib", 36)
    draw_text_centered(draw, "Apple", font_text, (255, 255, 255, 200), width, height, offset_y=90)
    return img

def generate_samsung(width, height):
    # Samsung signature blue gradient
    img = create_diagonal_gradient(width, height, (10, 75, 160), (5, 30, 75))
    draw = ImageDraw.Draw(img)
    
    # Inner border
    draw.rounded_rectangle([20, 20, width - 20, height - 20], radius=32, outline=(255, 255, 255, 30), width=3)
    
    # Rotated Samsung brand ellipse
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw_ov = ImageDraw.Draw(overlay)
    draw_ov.ellipse([60, 170, 452, 342], outline=(255, 255, 255, 220), width=8)
    
    # Rotate the ellipse by -12 degrees to represent the iconic Samsung shape
    overlay = overlay.rotate(-12, resample=Image.BICUBIC)
    img.alpha_composite(overlay)
    
    # Re-draw SAMSUNG wordmark exactly centered
    draw_main = ImageDraw.Draw(img)
    font_samsung = get_font("segoeuib", 50)
    draw_text_centered(draw_main, "SAMSUNG", font_samsung, (255, 255, 255), width, height, offset_y=0)
    return img

def generate_sony(width, height):
    # Sleek dark charcoal/black gradient
    img = create_diagonal_gradient(width, height, (30, 30, 30), (5, 5, 5))
    draw = ImageDraw.Draw(img)
    
    # Inner border
    draw.rounded_rectangle([20, 20, width - 20, height - 20], radius=32, outline=(255, 255, 255, 30), width=3)
    
    # Sony serif style text
    font_sony = get_font("georgiab", 90)
    draw_text_centered(draw, "SONY", font_sony, (255, 255, 255), width, height, offset_y=0)
    return img

def generate_adidas(width, height):
    # Bold black/grey gradient
    img = create_diagonal_gradient(width, height, (15, 15, 15), (45, 45, 45))
    draw = ImageDraw.Draw(img)
    
    # Inner border
    draw.rounded_rectangle([20, 20, width - 20, height - 20], radius=32, outline=(255, 255, 255, 30), width=3)
    
    # Three stripes drawn on transparent overlay and rotated
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw_ov = ImageDraw.Draw(overlay)
    
    # Draw three parallel rectangles
    draw_ov.rectangle([210, 170, 240, 290], fill=(255, 255, 255, 220))
    draw_ov.rectangle([260, 140, 290, 290], fill=(255, 255, 255, 220))
    draw_ov.rectangle([310, 110, 340, 290], fill=(255, 255, 255, 220))
    
    # Rotate overlay by 35 degrees
    overlay = overlay.rotate(35, resample=Image.BICUBIC)
    img.alpha_composite(overlay)
    
    # Adidas text below
    draw_main = ImageDraw.Draw(img)
    font_adidas = get_font("segoeui", 42)
    draw_text_centered(draw_main, "adidas", font_adidas, (255, 255, 255, 240), width, height, offset_y=95)
    return img

def generate_nike(width, height):
    # Dynamic red-orange energetic gradient
    img = create_diagonal_gradient(width, height, (231, 37, 36), (255, 87, 34))
    draw = ImageDraw.Draw(img)
    
    # Inner border
    draw.rounded_rectangle([20, 20, width - 20, height - 20], radius=32, outline=(255, 255, 255, 30), width=3)
    
    # Italic NIKE text
    font_nike = get_font("segoeuib", 100)
    draw_text_centered(draw, "NIKE", font_nike, (255, 255, 255), width, height, offset_y=-35)
    
    # Vector swoop underline curve
    swoosh_pts = [
        (130, 310), 
        (220, 325), 
        (370, 290), 
        (390, 260), 
        (330, 295), 
        (220, 315),
        (130, 310)
    ]
    draw.polygon(swoosh_pts, fill=(255, 255, 255))
    return img

def generate_prestige(width, height):
    # Ruby red premium gradient
    img = create_diagonal_gradient(width, height, (178, 31, 36), (100, 10, 15))
    draw = ImageDraw.Draw(img)
    
    # Inner gold border
    gold_color = (255, 215, 0, 150)
    draw.rounded_rectangle([20, 20, width - 20, height - 20], radius=32, outline=gold_color, width=4)
    
    # Crest outline
    draw.polygon([
        (256, 70), (370, 120), (340, 270), (256, 340), (172, 270), (142, 120)
    ], outline=gold_color, width=3)
    
    # Large serif 'P'
    font_p = get_font("georgiab", 140)
    draw_text_centered(draw, "P", font_p, (255, 255, 255), width, height, offset_y=-65)
    
    # Prestige wordmark
    font_text = get_font("georgiab", 40)
    draw_text_centered(draw, "Prestige", font_text, (255, 255, 255, 240), width, height, offset_y=110)
    return img

def generate_yonex(width, height):
    # Blue and Green athletic gradient
    img = create_diagonal_gradient(width, height, (0, 84, 166), (0, 135, 90))
    draw = ImageDraw.Draw(img)
    
    # Inner border
    draw.rounded_rectangle([20, 20, width - 20, height - 20], radius=32, outline=(255, 255, 255, 30), width=3)
    
    # White background block
    draw.rounded_rectangle([80, 180, 432, 332], radius=15, fill=(255, 255, 255))
    
    # YONEX wordmark in blue
    font_yonex = get_font("segoeuib", 55)
    draw_text_centered(draw, "YONEX", font_yonex, (0, 84, 166), width, height, offset_y=-3)
    
    # Yellow stripes
    draw.rectangle([80, 342, 250, 348], fill=(255, 220, 0))
    draw.rectangle([262, 342, 432, 348], fill=(255, 220, 0))
    return img

def generate_nintendo(width, height):
    # Gaming red gradient
    img = create_diagonal_gradient(width, height, (230, 0, 18), (160, 0, 10))
    draw = ImageDraw.Draw(img)
    
    # Inner border
    draw.rounded_rectangle([20, 20, width - 20, height - 20], radius=32, outline=(255, 255, 255, 30), width=3)
    
    # Oval outline
    draw.rounded_rectangle([70, 170, 442, 342], radius=85, outline=(255, 255, 255), width=8)
    
    # Nintendo typography
    font_nintendo = get_font("segoeuib", 52)
    draw_text_centered(draw, "Nintendo", font_nintendo, (255, 255, 255), width, height, offset_y=-5)
    return img

def generate_logitech(width, height):
    # Modern teal/cyan gradient
    img = create_diagonal_gradient(width, height, (0, 184, 230), (0, 100, 130))
    draw = ImageDraw.Draw(img)
    
    # Inner border
    draw.rounded_rectangle([20, 20, width - 20, height - 20], radius=32, outline=(255, 255, 255, 30), width=3)
    
    # Abstract corporate symbol
    draw.ellipse([180, 120, 300, 240], fill=(255, 255, 255, 240))
    draw.ellipse([210, 150, 290, 230], fill=(0, 130, 160))
    draw.ellipse([310, 120, 340, 150], fill=(255, 255, 255, 240))
    
    # Logitech text
    font_logi = get_font("segoeuib", 45)
    draw_text_centered(draw, "logitech", font_logi, (255, 255, 255), width, height, offset_y=90)
    return img

def generate_dell(width, height):
    # Corporate blue gradient
    img = create_diagonal_gradient(width, height, (0, 118, 192), (0, 60, 120))
    draw = ImageDraw.Draw(img)
    
    # Inner border
    draw.rounded_rectangle([20, 20, width - 20, height - 20], radius=32, outline=(255, 255, 255, 30), width=3)
    
    # Circle outline
    draw.ellipse([116, 116, 396, 396], outline=(255, 255, 255), width=7)
    
    # Dell lettering with tilted 'E'
    font_dell = get_font("segoeuib", 68)
    
    # D
    draw.text((165, 218), "D", fill=(255, 255, 255), font=font_dell)
    
    # Tilted E (draw on transparent canvas and rotate)
    e_img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    e_draw = ImageDraw.Draw(e_img)
    e_draw.text((25, 10), "E", fill=(255, 255, 255), font=font_dell)
    e_img_rot = e_img.rotate(-28, resample=Image.BICUBIC)
    img.alpha_composite(e_img_rot, dest=(198, 212))
    
    # L L
    draw.text((272, 218), "L", fill=(255, 255, 255), font=font_dell)
    draw.text((312, 218), "L", fill=(255, 255, 255), font=font_dell)
    return img

def generate_philips(width, height):
    # Royal blue gradient
    img = create_diagonal_gradient(width, height, (15, 94, 156), (11, 45, 80))
    draw = ImageDraw.Draw(img)
    
    # Inner border
    draw.rounded_rectangle([20, 20, width - 20, height - 20], radius=32, outline=(255, 255, 255, 30), width=3)
    
    # Shield shape outline
    shield_pts = [
        (160, 130), (352, 130), (352, 250), (256, 350), (160, 250), (160, 130)
    ]
    draw.polygon(shield_pts, outline=(255, 255, 255, 180), width=4)
    
    # Waves
    draw.arc([190, 270, 322, 310], start=30, end=150, fill=(255, 255, 255, 150), width=3)
    draw.arc([190, 290, 322, 330], start=30, end=150, fill=(255, 255, 255, 150), width=3)
    
    # Stars inside shield
    def draw_star(cx, cy, r):
        draw.line([(cx - r, cy), (cx + r, cy)], fill=(255, 255, 255, 200), width=2)
        draw.line([(cx, cy - r), (cx, cy + r)], fill=(255, 255, 255, 200), width=2)
        
    draw_star(210, 180, 8)
    draw_star(302, 180, 8)
    
    # Wordmark
    font_philips = get_font("segoeuib", 40)
    draw_text_centered(draw, "PHILIPS", font_philips, (255, 255, 255), width, height, offset_y=18)
    return img

def main():
    generators = {
        'apple': generate_apple,
        'samsung': generate_samsung,
        'sony': generate_sony,
        'adidas': generate_adidas,
        'nike': generate_nike,
        'prestige': generate_prestige,
        'yonex': generate_yonex,
        'nintendo': generate_nintendo,
        'logitech': generate_logitech,
        'dell': generate_dell,
        'philips': generate_philips,
    }
    
    print("Fetching brands from database...")
    brands = Brand.objects.all()
    if not brands.exists():
        print("No brands found in the database. Please make sure some brand records exist.")
        return
        
    for brand in brands:
        slug = brand.slug.lower()
        print(f"Processing Brand: {brand.name} (slug: {slug})...")
        
        generator = generators.get(slug)
        if not generator:
            print(f"No specific logo generator for slug '{slug}'. Using generic fallback.")
            def generic_generator(width, height):
                img = create_diagonal_gradient(width, height, (100, 100, 100), (40, 40, 40))
                draw = ImageDraw.Draw(img)
                draw.rounded_rectangle([20, 20, width - 20, height - 20], radius=32, outline=(255, 255, 255, 30), width=3)
                first_letter = brand.name[0].upper() if brand.name else "?"
                font_letter = get_font("georgiab", 180)
                draw_text_centered(draw, first_letter, font_letter, (255, 255, 255, 240), width, height, offset_y=-30)
                font_text = get_font("segoeuib", 36)
                draw_text_centered(draw, brand.name, font_text, (255, 255, 255, 200), width, height, offset_y=90)
                return img
            generator = generic_generator
            
        try:
            img = generator(512, 512)
            filename = f"{slug}.png"
            filepath = os.path.join(BRANDS_DIR, filename)
            
            img.save(filepath, "PNG")
            print(f"Saved logo image to {filepath}")
            
            # Align database model path
            db_path = f"brands/{filename}"
            if brand.image.name != db_path:
                brand.image.name = db_path
                brand.save()
                print(f"Updated brand {brand.name} database image path to {db_path}")
            else:
                brand.save()
                print(f"Brand {brand.name} database image path is already {db_path} (updated disk file)")
                
        except Exception as e:
            print(f"Error generating logo for {brand.name}: {e}")
            import traceback
            traceback.print_exc()

if __name__ == '__main__':
    main()
