import sqlite3
conn = sqlite3.connect(r'D:\Project\E-Commerce-Platform-Backend\db.sqlite3')
cur = conn.cursor()
print('PRODUCTS')
for row in cur.execute('SELECT id, name, slug FROM products_product ORDER BY id LIMIT 50'):
    print(row)
print('\nVARIANTS')
for row in cur.execute('SELECT id, product_id, name, sku FROM products_productvariant ORDER BY id LIMIT 50'):
    print(row)
conn.close()
