from flaredantic import FlareTunnel, TunnelConfig
import time

print("Запускаем туннель... Подожди 5-10 секунд.")
# Подключаемся к нашему локальному серверу на порту 5000
config = TunnelConfig(port=5000)

with FlareTunnel(config) as tunnel:
    print("\n" + "="*60)
    print("ТВОЯ ССЫЛКА ДЛЯ ТЕЛЕФОНА (скопируй её):")
    print(tunnel.tunnel_url)
    print("="*60 + "\n")
    print("Не закрывай это окно, пока пользуешься сайтом!")
    
    # Держим туннель открытым
    while True:
        time.sleep(1)