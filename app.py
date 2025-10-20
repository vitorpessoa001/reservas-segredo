from flask import Flask, render_template, request, redirect, url_for, jsonify
import sqlite3
from datetime import datetime, timedelta
import calendar

app = Flask(__name__)

# -----------------------------------------------------------
# Inicialização do banco de dados
# -----------------------------------------------------------
def init_db():
    conn = sqlite3.connect('reservas.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS reservas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chale TEXT NOT NULL,
            data_checkin TEXT NOT NULL,
            data_checkout TEXT NOT NULL,
            valor_diaria REAL NOT NULL,
            nome_cliente TEXT,
            status TEXT DEFAULT 'reservado',
            observacoes TEXT
        )
    ''')
    # Adicionar coluna observacoes se não existir
    try:
        c.execute('ALTER TABLE reservas ADD COLUMN observacoes TEXT')
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Coluna já existe
    conn.close()

def init_valores():
    conn = sqlite3.connect('reservas.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS valores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chale TEXT NOT NULL,
            data TEXT NOT NULL,
            valor REAL NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

# Adicionar filtro Jinja2 para formatar datas
@app.template_filter('format_date')
def format_date(value):
    if value:
        try:
            date_obj = datetime.strptime(value, '%Y-%m-%d')
            return date_obj.strftime('%d/%m/%Y')
        except ValueError:
            return value
    return ''

# -----------------------------------------------------------
# Página inicial (público)
# -----------------------------------------------------------
@app.route('/')
def index():
    return render_template('index.html')

# -----------------------------------------------------------
# Painel administrativo
# -----------------------------------------------------------
@app.route('/admin')
def admin():
    conn = sqlite3.connect('reservas.db')
    c = conn.cursor()
    c.execute("SELECT * FROM reservas ORDER BY data_checkin DESC")
    reservas = c.fetchall()
    conn.close()
    return render_template('admin.html', reservas=reservas)

# -----------------------------------------------------------
# API: Retorna o calendário com valores e status
# -----------------------------------------------------------
@app.route('/api/calendario/<chale>/<ano>/<mes>')
def get_calendario(chale, ano, mes):
    conn = sqlite3.connect('reservas.db')
    c = conn.cursor()

    # Consultar dias ocupados ou bloqueados para o chalé
    c.execute("""
        SELECT data_checkin, data_checkout, status, valor_diaria
        FROM reservas 
        WHERE chale=? AND strftime('%Y', data_checkin)=? AND strftime('%m', data_checkin)=?
    """, (chale, ano, mes.zfill(2)))
    reservas = c.fetchall()

    # Consultar valores diários
    c.execute("""
        SELECT data, valor FROM valores 
        WHERE chale=? AND strftime('%Y', data)=? AND strftime('%m', data)=?
    """, (chale, ano, mes.zfill(2)))
    valores = {row[0]: row[1] for row in c.fetchall()}

    # Gerar lista de dias do mês
    dias = []
    primeiro_dia = datetime(int(ano), int(mes), 1)
    ultimo_dia = datetime(int(ano), int(mes), calendar.monthrange(int(ano), int(mes))[1])

    current_date = primeiro_dia
    while current_date <= ultimo_dia:
        dia_iso = current_date.strftime('%Y-%m-%d')
        status = 'disponivel'
        valor = valores.get(dia_iso, 0)

        for reserva in reservas:
            checkin = reserva[0]
            checkout = reserva[1]
            if checkin <= dia_iso <= checkout:
                status = 'ocupado' if reserva[2] == 'reservado' else 'bloqueado'
                valor = float(reserva[3]) if reserva[3] else valor
                break

        dias.append({
            'data': dia_iso,
            'status': status,
            'valor': valor
        })
        current_date += timedelta(days=1)

    conn.close()
    return jsonify(dias)

# -----------------------------------------------------------
# API: Atualizar valores diários (rota usada pelo admin)
# -----------------------------------------------------------
@app.route('/api/definir_valor', methods=['POST'])
def definir_valor():
    data = request.get_json()
    chale = data.get('chale')
    datas = data.get('datas', [])
    valor = data.get('valor')

    if not (chale and datas and valor is not None):
        return jsonify({'erro': 'Parâmetros inválidos'}), 400

    conn = sqlite3.connect('reservas.db')
    c = conn.cursor()

    for data_str in datas:
        # Remove valor anterior (se existir)
        c.execute('DELETE FROM valores WHERE chale=? AND data=?', (chale, data_str))
        # Insere novo valor
        c.execute('INSERT INTO valores (chale, data, valor) VALUES (?, ?, ?)', (chale, data_str, valor))

    conn.commit()
    conn.close()

    return jsonify({'status': 'ok'})

# -----------------------------------------------------------
# API: Bloquear / Desbloquear datas
# -----------------------------------------------------------
@app.route('/api/bloquear', methods=['POST'])
def bloquear_datas():
    data = request.get_json()
    chale = data.get('chale')
    datas = data.get('datas', [])

    if not (chale and datas):
        return jsonify({'erro': 'Parâmetros inválidos'}), 400

    conn = sqlite3.connect('reservas.db')
    c = conn.cursor()

    for dia in datas:
        # Verifica se já existe bloqueio que inclua essa data
        c.execute("""
            SELECT id FROM reservas 
            WHERE chale=? AND status='bloqueado' 
            AND data_checkin <= ? AND data_checkout >= ?
        """, (chale, dia, dia))
        bloqueio = c.fetchone()

        if bloqueio:
            # Remove o bloqueio inteiro
            c.execute("DELETE FROM reservas WHERE id = ?", (bloqueio[0],))
            print(f"Desbloqueado período para dia {dia} (ID: {bloqueio[0]})")
        else:
            # Insere novo bloqueio de 1 dia com status 'bloqueado'
            c.execute('''
                INSERT INTO reservas (chale, data_checkin, data_checkout, valor_diaria, nome_cliente, status, observacoes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (chale, dia, dia, 0, 'Bloqueado', 'bloqueado', 'Bloqueio simples'))

    conn.commit()
    conn.close()

    return jsonify({'status': 'ok'})

# -----------------------------------------------------------
# Rota para adicionar/editar reserva
# -----------------------------------------------------------
@app.route('/add_reserva', methods=['POST'])
def add_reserva():
    chale = request.form['chale']
    checkin = request.form['data_checkin']
    checkout = request.form['data_checkout']
    diaria = float(request.form.get('valor_diaria', 0))
    observacoes = request.form.get('observacoes', '')
    reserva_id = request.form.get('reserva_id', '')

    conn = sqlite3.connect('reservas.db')
    c = conn.cursor()

    if reserva_id:  # Editar reserva existente
        c.execute('''
            UPDATE reservas 
            SET chale = ?, data_checkin = ?, data_checkout = ?, valor_diaria = ?, observacoes = ?
            WHERE id = ?
        ''', (chale, checkin, checkout, diaria, observacoes, reserva_id))
    else:  # Criar nova reserva
        c.execute('''
            INSERT INTO reservas (chale, data_checkin, data_checkout, valor_diaria, nome_cliente, observacoes)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (chale, checkin, checkout, diaria, '', observacoes))

    conn.commit()
    conn.close()

    return redirect(url_for('admin'))

# -----------------------------------------------------------
# API: Excluir reserva
# -----------------------------------------------------------
@app.route('/api/excluir_reserva', methods=['POST'])
def excluir_reserva():
    data = request.get_json()
    reserva_id = data.get('reserva_id')

    if not reserva_id:
        return jsonify({'erro': 'Parâmetro inválido'}), 400

    conn = sqlite3.connect('reservas.db')
    c = conn.cursor()
    c.execute('DELETE FROM reservas WHERE id = ?', (reserva_id,))
    conn.commit()
    conn.close()

    return jsonify({'status': 'ok'})

# -----------------------------------------------------------
# Execução principal
# -----------------------------------------------------------
if __name__ == '__main__':
    init_db()
    init_valores()
    app.run(debug=True)