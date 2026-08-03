"""
GL Reconciliation Web Application

Flask app for uploading RVW and Craftable exports, reconciling invoices,
and exporting discrepancies for accounting review.
"""

from flask import Flask, render_template, request, send_file, jsonify
import io
import logging
from parsers import parse_rvw, parse_craftable, FileValidationError
from reconciler import Reconciler
from exporters import export_selected_results_to_excel

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB max file size

# Store results in session (stateless for now, can upgrade to session storage later)
_last_results = None
_last_threshold = None


@app.route('/', methods=['GET', 'POST'])
def index():
    """Main upload form."""
    if request.method == 'POST':
        return handle_upload()
    return render_template('upload.html')


def handle_upload():
    """Process uploaded files and display reconciliation results."""
    global _last_results, _last_threshold
    
    try:
        # Get uploaded files
        rvw_file = request.files.get('rvw')
        food_file = request.files.get('food')
        bev_file = request.files.get('bev')
        
        # Validate files uploaded
        if not all([rvw_file, food_file, bev_file]):
            return render_template('upload.html', error='All three files are required')
        
        # Get threshold parameter
        try:
            threshold = float(request.form.get('threshold', 5.0))
        except ValueError:
            return render_template('upload.html', error='Threshold must be a number')
        
        if threshold < 0:
            return render_template('upload.html', error='Threshold must be >= 0')
        
        # Parse files
        df_rvw = parse_rvw(rvw_file)
        df_craftable = parse_craftable(food_file, bev_file)
        
        # Reconcile
        reconciler = Reconciler(threshold=threshold)
        results = reconciler.reconcile(df_rvw, df_craftable)
        
        # Store results for export
        _last_results = results
        _last_threshold = threshold
        
        # Group by GL code, with each group sorted by date (per BK's export spec:
        # sort by GL code, then date)
        grouped = reconciler.group_by_gl()
        for gl_code in grouped:
            grouped[gl_code].sort(key=lambda r: r.date)
        
        # Calculate summaries
        gl_summaries = {}
        for gl_code in sorted(grouped.keys()):
            gl_summaries[gl_code] = reconciler.get_summary_for_gl(gl_code)
        
        # Split invoices (span >1 GL code) get their own total + per-GL breakdown view
        split_invoices = reconciler.get_split_invoices()
        
        return render_template(
            'results.html',
            grouped_results=grouped,
            gl_summaries=gl_summaries,
            split_invoices=split_invoices,
            threshold=threshold,
            total_results=len(results),
            total_matched=sum(1 for r in results if r.match_type == 'matched'),
            total_unmatched=sum(1 for r in results if r.match_type != 'matched')
        )
    
    except FileValidationError as e:
        logger.error(f"File validation error: {str(e)}")
        return render_template('upload.html', error=f"File Error: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        return render_template('upload.html', error=f"Error: {str(e)}")


@app.route('/export', methods=['POST'])
def export():
    """Export selected results to Excel."""
    global _last_results
    
    try:
        if not _last_results:
            return jsonify({'error': 'No reconciliation results to export'}), 400
        
        # Get selected invoices from form
        selected_keys = request.form.getlist('selected_invoices')
        
        if not selected_keys:
            return jsonify({'error': 'No invoices selected for export'}), 400
        
        # Create Excel file in memory
        buffer = io.BytesIO()
        export_selected_results_to_excel(buffer, _last_results, selected_keys)
        buffer.seek(0)
        
        return send_file(
            buffer,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='GL_Reconciliation_Report.xlsx'
        )
    
    except Exception as e:
        logger.error(f"Export error: {str(e)}", exc_info=True)
        return jsonify({'error': f"Export failed: {str(e)}"}), 500


@app.errorhandler(413)
def too_large(e):
    """Handle file too large error."""
    return render_template('upload.html', error='File too large (max 50 MB)'), 413


@app.errorhandler(404)
def not_found(e):
    """Handle 404 errors."""
    return render_template('upload.html', error='Page not found'), 404


@app.errorhandler(500)
def server_error(e):
    """Handle 500 errors."""
    logger.error(f"Server error: {str(e)}", exc_info=True)
    return render_template('upload.html', error='Server error - please try again'), 500


if __name__ == '__main__':
    # Debug mode OFF for production
    app.run(
        host='127.0.0.1',
        port=5000,
        debug=False
    )
