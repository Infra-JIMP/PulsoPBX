"""Teste isolado: valida login na AMI do MikoPBX e lista os ramais atuais,
sem rodar o monitor completo. Rode este script primeiro, ao criar/trocar credenciais."""
import asyncio
import sys

from panoramisk import Manager

from config import load_config


async def main() -> None:
    config = load_config()
    manager = Manager(
        host=config.ami_host,
        port=config.ami_port,
        username=config.ami_user,
        secret=config.ami_secret,
    )

    connected = {"ok": False}

    def on_login(mngr: Manager) -> None:
        connected["ok"] = True
        print(f"[OK] Login AMI bem-sucedido em {config.ami_host}:{config.ami_port} como '{config.ami_user}'")

    manager.on_login = on_login

    await manager.connect()
    await asyncio.sleep(1)  # da tempo do handshake/login completar

    if not connected["ok"]:
        print("[ERRO] Conectou no socket mas o login nao foi confirmado.")
        print("       Verifique usuario/senha e o Network Filter no MikoPBX.")
        manager.close()
        sys.exit(1)

    print("Consultando ramais via ExtensionStateList...")
    count = 0
    messages = await manager.send_action({"Action": "ExtensionStateList"}, as_list=True)
    for message in messages:
        if message.Event == "ExtensionStatus" and message.Exten:
            count += 1
            print(f"  Ramal {message.Exten}: {message.StatusText or 'desconhecido'}")

    print(f"Total de ramais encontrados: {count}")
    manager.close()


if __name__ == "__main__":
    asyncio.run(main())
