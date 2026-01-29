import os
from dotenv import load_dotenv
from app import app, db, Usuario, Sucursal, Empleado, Servicio, Producto, ReglaPuntos
from werkzeug.security import generate_password_hash

def inicializar_sistema():
    # Asegurar que la carpeta instance exista si usas SQLite en instance/
    if not os.path.exists("instance"):
        os.makedirs("instance")
        print("📁 Carpeta 'instance' creada.")

    db_path = "instance/barberia.db" # Ajusta a tu ruta real
    
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
            print(f"🗑️ Base de datos antigua eliminada.")
        except Exception as e:
            print(f"❌ Error al borrar: {e}")
            return

    with app.app_context():
        db.create_all()
        
        # 1. Admin Maestro
        admin = Usuario(
            nombre="Admin General",
            email="admin@barberia.com",
            password=generate_password_hash("Admin123!"),
            rol="admin"
        )
        db.session.add(admin)
        db.session.commit() # Commiteamos para tener el ID del admin

        # 2. Sucursal
        sucursal = Sucursal(nombre="Sede Central", direccion="Calle 123")
        db.session.add(sucursal)
        db.session.commit()


        db.session.commit()
        print("🚀 SISTEMA REINICIADO EXITOSAMENTE")

if __name__ == "__main__":
    inicializar_sistema()