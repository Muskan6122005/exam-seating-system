import streamlit as st
import sqlite3
import pandas as pd
from typing import Dict, Any, List, Tuple

# --- Configuration ---
DATABASE_NAME = "exam_seating.db"
DEFAULT_ROWS = 10
DEFAULT_COLS = 15
MAX_ROWS = 26
MAX_COLS = 40

# --- Database helpers (robust to schema differences) ---

def initialize_database():
    """
    Create tables if not present.
    Note: if an older 'halls' table exists with a different schema, we do NOT alter it here.
    """
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()

    # Create halls table with rows/cols columns for new installs.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS halls (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            rows INTEGER,
            cols INTEGER,
            capacity INTEGER
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS assignments (
            assignment_id INTEGER PRIMARY KEY,
            hall_id INTEGER NOT NULL,
            seat_id TEXT NOT NULL UNIQUE,
            student_id TEXT NOT NULL,
            FOREIGN KEY (hall_id) REFERENCES halls(id)
        )
    """)

    # Insert sample halls only if empty
    cursor.execute("SELECT COUNT(*) FROM halls")
    if cursor.fetchone()[0] == 0:
        sample = [
            ("Main Hall A", DEFAULT_ROWS, DEFAULT_COLS, DEFAULT_ROWS * DEFAULT_COLS),
            ("Small Room B", 8, 10, 8 * 10),
            ("Auditorium C", 12, 20, 12 * 20)
        ]
        cursor.executemany("INSERT INTO halls (name, rows, cols, capacity) VALUES (?, ?, ?, ?)", sample)

    conn.commit()
    conn.close()

def _get_table_columns(conn: sqlite3.Connection, table_name: str) -> List[str]:
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({table_name})")
    rows = cursor.fetchall()
    return [r[1] for r in rows]  # name is at index 1

def get_halls() -> pd.DataFrame:
    """
    Return a dataframe with columns: id, name, rows, cols, capacity (if present).
    If rows/cols missing in DB, supply defaults or estimate from capacity.
    """
    conn = sqlite3.connect(DATABASE_NAME)
    cols = _get_table_columns(conn, "halls")
    cursor = conn.cursor()

    # If the table has rows and cols, select them
    if "rows" in cols and "cols" in cols:
        cursor.execute("SELECT id, name, rows, cols, capacity FROM halls")
        records = cursor.fetchall()
        df = pd.DataFrame(records, columns=["id", "name", "rows", "cols", "capacity"])
        # If rows/cols are NULL, fill with defaults
        df["rows"] = df["rows"].fillna(DEFAULT_ROWS).astype(int)
        df["cols"] = df["cols"].fillna(DEFAULT_COLS).astype(int)
    else:
        # Old schema: maybe only name + capacity exist, or only name
        if "capacity" in cols:
            cursor.execute("SELECT id, name, capacity FROM halls")
            records = cursor.fetchall()
            df = pd.DataFrame(records, columns=["id", "name", "capacity"])
            # Estimate rows/cols from capacity (attempt to keep DEFAULT_ROWS if possible)
            def estimate(rcap):
                try:
                    cap = int(rcap) if rcap is not None else DEFAULT_ROWS * DEFAULT_COLS
                except Exception:
                    cap = DEFAULT_ROWS * DEFAULT_COLS
                rows = DEFAULT_ROWS
                cols = max(1, cap // rows)
                if cols > MAX_COLS:
                    cols = DEFAULT_COLS
                return rows, cols
            df["rows"], df["cols"] = zip(*df["capacity"].map(estimate))
            df = df[["id", "name", "rows", "cols", "capacity"]]
        else:
            # Minimal fallback: table exists with only id,name (very old)
            cursor.execute("SELECT id, name FROM halls")
            records = cursor.fetchall()
            df = pd.DataFrame(records, columns=["id", "name"])
            df["rows"] = DEFAULT_ROWS
            df["cols"] = DEFAULT_COLS
            df["capacity"] = DEFAULT_ROWS * DEFAULT_COLS

    conn.close()
    return df

def get_assigned_seats(hall_id: int) -> Dict[str, str]:
    """Return mapping seat_id -> student_id for a given hall."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT seat_id, student_id FROM assignments WHERE hall_id = ?", (hall_id,))
    rows = cursor.fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}

def assign_seats(hall_id: int, selected_seats: List[str], student_id: str) -> Tuple[bool, str]:
    """Assign the selected seats to the student. Returns (success,msg)."""
    if not selected_seats or not student_id:
        return False, "No seats selected or Student ID missing."

    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    try:
        existing = get_assigned_seats(hall_id)
        conflicts = [s for s in selected_seats if s in existing]
        if conflicts:
            return False, f"Assignment conflict: {', '.join(conflicts)} are already assigned."

        data = [(hall_id, s, student_id) for s in selected_seats]
        cursor.executemany(
            "INSERT INTO assignments (hall_id, seat_id, student_id) VALUES (?, ?, ?)",
            data
        )
        conn.commit()
        return True, f"Successfully assigned {len(selected_seats)} seat(s) to {student_id}."
    except Exception as e:
        conn.rollback()
        return False, f"DB error: {e}"
    finally:
        conn.close()

# --- UI / Helper logic ---

def is_valid_distancing_seat(row_idx: int, col_idx: int) -> bool:
    return (row_idx + col_idx) % 2 == 0

def render_seating_chart(
    hall_id: int,
    assigned_seats: Dict[str, str],
    num_rows: int,
    num_cols: int,
    disabled: bool = False
) -> List[str]:
    """
    Render the seating chart and return the seats currently selected in session state.
    num_rows/num_cols are used from session state (temporary visualization).
    """
    st.subheader("Seating Chart — Alternating Pattern Enforced")
    st.markdown("""
    <style>
    .screen-box {
        background-color:#007BFF; color:white; padding:12px; text-align:center;
        border-radius:10px 10px 0 0; margin-bottom: 12px; font-weight:bold;
    }
    .stButton>button { font-size: 1rem; padding:4px; margin:3px; width:36px; height:36px; border-radius:6px; }
    .scrollable { overflow-x:auto; padding-bottom: 8px; }
    </style>
    <div class="screen-box">EXAMINATION FRONT / STAGE</div>
    """, unsafe_allow_html=True)

    # prepare session state for temporary selections
    if 'temp_selected_seats' not in st.session_state:
        st.session_state['temp_selected_seats'] = []

    # generate row letters A..Z (cap at MAX_ROWS)
    rows = [chr(65 + i) for i in range(min(num_rows, MAX_ROWS))]
    selected_now: List[str] = []

    # Keep the seating grid in a scrollable container to avoid squish on narrow screens
    st.markdown('<div class="scrollable">', unsafe_allow_html=True)
    cols_layout = st.columns([0.5] + [1] * num_cols)
    cols_layout[0].markdown("")  # header gap for row label
    for c in range(num_cols):
        cols_layout[c + 1].markdown(f"**{c+1}**")

    for r_idx, r_letter in enumerate(rows):
        cols_layout[0].markdown(f"**{r_letter}**")
        for c_idx in range(num_cols):
            seat_id = f"{r_letter}{c_idx+1}"
            is_assigned = seat_id in assigned_seats
            valid_spot = is_valid_distancing_seat(r_idx, c_idx)
            is_temp_selected = seat_id in st.session_state['temp_selected_seats']
            key = f"seat_{hall_id}_{seat_id}"

            # Decide label & disabled state
            if is_assigned:
                label = "🧑‍🎓"
                tooltip = f"Assigned to {assigned_seats[seat_id]}"
                btn_disabled = True
            elif not valid_spot:
                label = "🚫"
                tooltip = "Blocked by Distancing Rule"
                btn_disabled = True
            elif is_temp_selected:
                label = "✅"
                tooltip = "Selected for assignment"
                btn_disabled = disabled
            else:
                label = "⚪"
                tooltip = "Available"
                btn_disabled = disabled

            if cols_layout[c_idx + 1].button(label, key=key, help=tooltip, disabled=btn_disabled):
                # Toggle selection
                if not is_assigned and valid_spot and not disabled:
                    if is_temp_selected:
                        st.session_state['temp_selected_seats'].remove(seat_id)
                    else:
                        st.session_state['temp_selected_seats'].append(seat_id)
                    st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)

    # Compose selected list to return (only seats that remain valid against the current assigned list)
    selected_now = [s for s in st.session_state['temp_selected_seats'] if s not in assigned_seats]
    return selected_now

# --- Main App ---

def main():
    st.set_page_config(page_title="Exam Seating System", layout="wide")
    st.title("👨‍🏫 Exam Seating Assignment (Temporary Layout Mode)")

    initialize_database()

    # Session state for confirm flow and temp layout overrides
    st.session_state.setdefault('show_confirmation', False)
    st.session_state.setdefault('confirm_student_id', "")
    st.session_state.setdefault('confirm_selected_seats', [])
    st.session_state.setdefault('layout_override', {})  # mapping hall_id -> (rows,cols)

    halls_df = get_halls()
    if halls_df.empty:
        st.error("No halls found in the database.")
        return

    hall_names = halls_df['name'].tolist()
    selected_hall_name = st.selectbox("Select Examination Hall:", hall_names, index=0)
    selected_row = halls_df[halls_df['name'] == selected_hall_name].iloc[0]

    hall_id = int(selected_row['id'])
    hall_rows = int(selected_row.get('rows', DEFAULT_ROWS))
    hall_cols = int(selected_row.get('cols', DEFAULT_COLS))
    hall_capacity = int(selected_row.get('capacity', hall_rows * hall_cols))

    st.info(f"Hall: **{selected_row['name']}** | DB layout: {hall_rows} rows × {hall_cols} cols | Capacity (if present): {hall_capacity}")

    # --- Temporary Adjust Room Layout (B: not saved to DB) ---
    with st.expander("🛠 Adjust Room Layout (Temporary — Not Saved)"):
        # If user previously adjusted layout for this hall in this session, use it
        if hall_id in st.session_state['layout_override']:
            cur_rows, cur_cols = st.session_state['layout_override'][hall_id]
        else:
            cur_rows, cur_cols = hall_rows, hall_cols

        new_rows = st.number_input("Rows", min_value=1, max_value=MAX_ROWS, value=cur_rows, key=f"rows_{hall_id}")
        new_cols = st.number_input("Columns", min_value=1, max_value=MAX_COLS, value=cur_cols, key=f"cols_{hall_id}")

        col1, col2 = st.columns([1, 1])
        if col1.button("Apply (Temporary)", key=f"apply_{hall_id}"):
            st.session_state['layout_override'][hall_id] = (int(new_rows), int(new_cols))
            st.success(f"Applied temporary layout: {new_rows} rows × {new_cols} cols (will not be saved to DB).")
            st.rerun()
        if col2.button("Reset to DB layout", key=f"reset_{hall_id}"):
            if hall_id in st.session_state['layout_override']:
                del st.session_state['layout_override'][hall_id]
            st.success("Reset to the layout from the database.")
            st.rerun() # <-- FIXED: was st.experimental_rerun()

    # Use override if present, otherwise DB values
    if hall_id in st.session_state['layout_override']:
        display_rows, display_cols = st.session_state['layout_override'][hall_id]
    else:
        display_rows, display_cols = hall_rows, hall_cols

    is_confirming = st.session_state['show_confirmation']

    student_id_input = st.text_input("Enter Student ID to Assign:", placeholder="E.g., S12345", disabled=is_confirming).strip()

    assigned_seats = get_assigned_seats(hall_id)
    selected_seats = render_seating_chart(hall_id, assigned_seats, display_rows, display_cols, disabled=is_confirming)

    st.markdown("---")
    # Summary & Review (only when not confirming)
    if not is_confirming:
        st.header("Assignment Summary")
        col_a, col_b = st.columns(2)
        col_a.metric("Selected Seats", len(selected_seats))
        col_b.metric("Seats Already Assigned", len(assigned_seats))

        if len(selected_seats) > 0 and student_id_input:
            st.success(f"Seats ready to be assigned to **{student_id_input}**: **{', '.join(selected_seats)}**")
        elif student_id_input and len(selected_seats) == 0:
            st.warning("Please select at least one seat to assign the student.")

        # Review Button (locks the selection to confirm)
        rev_col1, rev_col2, rev_col3 = st.columns([1, 2, 1])
        with rev_col2:
            if st.button("Review Assignment", disabled=(len(selected_seats) == 0 or not student_id_input), key="review_button"):
                st.session_state['show_confirmation'] = True
                st.session_state['confirm_student_id'] = student_id_input
                st.session_state['confirm_selected_seats'] = selected_seats.copy()
                st.rerun() # <-- FIXED: was st.experimental_rerun()

    # --- Confirmation Form (clean, no ghost button) ---
    if st.session_state['show_confirmation']:
        confirm_student = st.session_state['confirm_student_id']
        confirm_seats = st.session_state['confirm_selected_seats']

        st.markdown("### Confirm Assignment")
        st.write(f"You are about to assign **{len(confirm_seats)}** seat(s): `{', '.join(confirm_seats)}` to **{confirm_student}**.")
        st.warning("Confirming will write the assignment(s) to the database (irreversible here).")

        with st.form("confirmation_form"):
            ccol, xcol = st.columns(2)
            confirm_btn = ccol.form_submit_button("✅ Confirm & Assign", use_container_width=True)
            cancel_btn = xcol.form_submit_button("❌ Cancel", use_container_width=True)

            if confirm_btn:
                # Re-check conflicts before committing
                with st.spinner("Processing assignment..."):
                    success, msg = assign_seats(hall_id, confirm_seats, confirm_student)
                if success:
                    st.success(msg)
                    # clear temp selection & confirmation
                    st.session_state['temp_selected_seats'] = []
                    st.session_state['show_confirmation'] = False
                    st.session_state['confirm_student_id'] = ""
                    st.session_state['confirm_selected_seats'] = []
                    st.rerun() # <-- FIXED: was st.experimental_rerun()
                else:
                    st.error(msg)
                    st.session_state['show_confirmation'] = False
                    st.rerun() # <-- FIXED: was st.experimental_rerun()
            elif cancel_btn:
                st.session_state['show_confirmation'] = False
                st.session_state['confirm_student_id'] = ""
                st.session_state['confirm_selected_seats'] = []
                st.rerun() # <-- FIXED: was st.experimental_rerun()


if __name__ == "__main__":
    main()