from flaredantic import ServeoTunnel, ServeoConfig
import time

print("Запускаем Serveo туннель...")
# Подключаемся к нашему локальному серверу на порту 5000
config = ServeoConfig(port=5000)

try:
    with ServeoTunnel(config) as tunnel:
        print("\n" + "="*60)
        print("ТВОЯ НОВАЯ ССЫЛКА ДЛЯ ТЕЛЕФОНА:")
        print(tunnel.tunnel_url)
        print("="*60 + "\n")
        print("Оставь это окно открытым!")
        
        while True:
            time.sleep(1)
except Exception as e:
    print(f"\nОшибка Serveo: {e}")