"""
Script de conversion Markdown vers PDF pour le projet BASTET
Utilise fpdf2 avec support des tableaux multi-lignes
"""

from fpdf import FPDF
import re

class PDF(FPDF):
    def __init__(self):
        super().__init__()
        self.add_font('DejaVu', '', 'C:/Windows/Fonts/arial.ttf')
        self.add_font('DejaVu', 'B', 'C:/Windows/Fonts/arialbd.ttf')
        self.add_font('DejaVu', 'I', 'C:/Windows/Fonts/ariali.ttf')
    
    def header(self):
        self.set_font('DejaVu', 'B', 10)
        self.set_text_color(63, 81, 181)
        self.cell(0, 10, 'Projet BASTET - Robot IA Vision Contextuelle', align='C')
        self.ln(15)
    
    def footer(self):
        self.set_y(-15)
        self.set_font('DejaVu', 'I', 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f'Page {self.page_no()}', align='C')

def clean_text(text):
    """Nettoie le texte des caracteres speciaux"""
    replacements = {
        '✅': '[OK]', '❌': '[X]', '🔄': '[~]', '→': '->', 
        '◄': '<', '►': '>', '─': '-', '│': '|',
        '┌': '+', '┐': '+', '└': '+', '┘': '+'
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text

def render_table(pdf, table_data):
    """Rendu d'un tableau avec support multi-lignes"""
    if not table_data or not table_data[0]:
        return
    
    num_cols = len(table_data[0])
    page_width = 190
    col_widths = [page_width / num_cols] * num_cols
    
    # Calculer les hauteurs de chaque ligne
    line_height = 6
    
    for row_idx, row in enumerate(table_data):
        is_header = (row_idx == 0)
        
        # Calculer la hauteur necessaire pour cette ligne
        max_lines = 1
        cell_texts = []
        for cell in row:
            text = re.sub(r'\*\*([^*]+)\*\*', r'\1', cell)
            text = clean_text(text)
            cell_texts.append(text)
            # Estimer le nombre de lignes
            chars_per_line = int(col_widths[0] / 2)  # ~2 chars par mm
            if chars_per_line > 0:
                lines_needed = max(1, len(text) // chars_per_line + 1)
                max_lines = max(max_lines, lines_needed)
        
        row_height = max(line_height * max_lines, 8)
        
        # Verifier si on a besoin d'une nouvelle page
        if pdf.get_y() + row_height > 280:
            pdf.add_page()
        
        # Style selon header ou data
        if is_header:
            pdf.set_fill_color(63, 81, 181)
            pdf.set_text_color(255, 255, 255)
            pdf.set_font('DejaVu', 'B', 9)
        else:
            if row_idx % 2 == 0:
                pdf.set_fill_color(245, 245, 245)
            else:
                pdf.set_fill_color(255, 255, 255)
            pdf.set_text_color(0, 0, 0)
            pdf.set_font('DejaVu', '', 9)
        
        # Position de depart de la ligne
        start_x = pdf.get_x()
        start_y = pdf.get_y()
        
        # Dessiner chaque cellule
        for col_idx, text in enumerate(cell_texts):
            x = start_x + sum(col_widths[:col_idx])
            pdf.set_xy(x, start_y)
            
            # Dessiner le fond de la cellule
            pdf.rect(x, start_y, col_widths[col_idx], row_height, 'DF')
            
            # Ecrire le texte avec marge interne
            pdf.set_xy(x + 1, start_y + 1)
            
            # Multi-cell pour le texte
            pdf.multi_cell(col_widths[col_idx] - 2, line_height, text, border=0, align='L')
        
        # Aller a la ligne suivante
        pdf.set_xy(start_x, start_y + row_height)
    
    pdf.ln(5)
    pdf.set_text_color(0, 0, 0)

def parse_markdown_to_pdf(md_file, pdf_file):
    pdf = PDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    
    with open(md_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    content = clean_text(content)
    lines = content.split('\n')
    in_code_block = False
    in_table = False
    table_data = []
    
    for i, line in enumerate(lines):
        line = line.rstrip()
        
        # Code blocks
        if line.startswith('```'):
            in_code_block = not in_code_block
            continue
        
        if in_code_block:
            pdf.set_font('DejaVu', '', 8)
            pdf.set_fill_color(240, 240, 240)
            pdf.set_text_color(50, 50, 50)
            pdf.cell(0, 5, line[:95], fill=True, new_x='LMARGIN', new_y='NEXT')
            pdf.set_text_color(0, 0, 0)
            continue
        
        # Tables - detection
        if '|' in line and not line.startswith('```'):
            # Ligne de separation (---) - ignorer
            if re.match(r'^[\|\s\-:]+$', line):
                continue
            
            cells = [c.strip() for c in line.split('|')]
            # Enlever les cellules vides au debut/fin
            cells = [c for c in cells if c]
            
            if cells:
                if not in_table:
                    in_table = True
                    table_data = []
                table_data.append(cells)
            continue
        
        # Fin de tableau - rendre le tableau
        if in_table and table_data:
            render_table(pdf, table_data)
            table_data = []
            in_table = False
        
        # Skip empty lines
        if not line.strip():
            pdf.ln(3)
            continue
        
        # Headers
        if line.startswith('# '):
            pdf.set_font('DejaVu', 'B', 18)
            pdf.set_text_color(26, 35, 126)
            text = line[2:].strip()
            pdf.cell(0, 12, text, new_x='LMARGIN', new_y='NEXT')
            pdf.set_draw_color(63, 81, 181)
            pdf.line(10, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(5)
            continue
        
        if line.startswith('## '):
            pdf.set_font('DejaVu', 'B', 14)
            pdf.set_text_color(48, 63, 159)
            text = line[3:].strip()
            pdf.ln(5)
            pdf.cell(0, 10, text, new_x='LMARGIN', new_y='NEXT')
            continue
        
        if line.startswith('### '):
            pdf.set_font('DejaVu', 'B', 12)
            pdf.set_text_color(63, 81, 181)
            text = line[4:].strip()
            pdf.cell(0, 8, text, new_x='LMARGIN', new_y='NEXT')
            continue
        
        # Horizontal rule
        if line.startswith('---'):
            pdf.set_draw_color(200, 200, 200)
            pdf.line(10, pdf.get_y() + 3, 200, pdf.get_y() + 3)
            pdf.ln(8)
            continue
        
        # Lists with checkboxes
        if line.startswith('- [') or line.startswith('  - ['):
            pdf.set_font('DejaVu', '', 10)
            pdf.set_text_color(0, 0, 0)
            indent = 15 if line.startswith('  ') else 10
            
            if '[x]' in line:
                marker = '[v] '
                pdf.set_text_color(0, 128, 0)
            elif '[/]' in line:
                marker = '[~] '
                pdf.set_text_color(255, 165, 0)
            else:
                marker = '[ ] '
                pdf.set_text_color(128, 128, 128)
            
            text = re.sub(r'\[.\]', '', line).strip().lstrip('- ')
            pdf.set_x(indent)
            pdf.multi_cell(0, 6, marker + text)
            pdf.set_text_color(0, 0, 0)
            continue
        
        # Regular lists
        if line.startswith('- ') or line.startswith('* '):
            pdf.set_font('DejaVu', '', 10)
            pdf.set_text_color(0, 0, 0)
            text = line[2:].strip()
            text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
            pdf.set_x(15)
            pdf.multi_cell(0, 6, '* ' + text)
            continue
        
        # Numbered lists
        if re.match(r'^\d+\.', line):
            pdf.set_font('DejaVu', '', 10)
            pdf.set_text_color(0, 0, 0)
            text = re.sub(r'\*\*([^*]+)\*\*', r'\1', line)
            pdf.set_x(12)
            pdf.multi_cell(0, 6, text)
            continue
        
        # Regular paragraph
        pdf.set_font('DejaVu', '', 10)
        pdf.set_text_color(0, 0, 0)
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', line)
        text = re.sub(r'`([^`]+)`', r'\1', text)
        text = text.replace('*', '')
        
        if text.strip():
            pdf.multi_cell(0, 6, text)
    
    # Rendre le dernier tableau si present
    if in_table and table_data:
        render_table(pdf, table_data)
    
    pdf.output(pdf_file)
    print(f'PDF cree avec succes: {pdf_file}')

if __name__ == '__main__':
    parse_markdown_to_pdf('documentation_projet.md', 'documentation_bastet.pdf')
    print('Conversion terminee!')
