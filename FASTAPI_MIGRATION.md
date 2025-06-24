# FastAPI Migration for Kospex Web

This document outlines the migration from Flask (`kweb.py`) to FastAPI (`kweb2.py`).

## Status

✅ **Completed**: Basic FastAPI structure with summary endpoints
🔶 **In Progress**: Full feature parity with Flask version
❌ **Pending**: Migration of remaining endpoints

## Migrated Endpoints

| Endpoint | Flask Route | FastAPI Route | Status |
|----------|-------------|---------------|---------|
| Home/Summary | `@app.route('/')` | `@app.get("/")` | ✅ Complete |
| Summary with ID | `@app.route('/summary/<id>')` | `@app.get("/summary/{id}")` | ✅ Complete |
| Help Pages | `@app.route('/help/<id>')` | `@app.get("/help/{id}")` | ✅ Complete |
| Active Developers | `@app.route('/developers/active/<repo_id>')` | `@app.get("/developers/active/{repo_id}")` | ✅ Complete |
| Servers | `@app.route('/servers/')` | `@app.get("/servers/")` | ✅ Complete |
| Metadata | `@app.route('/metadata/')` | `@app.get("/metadata/")` | ✅ Complete |
| Orphans | `@app.route('/orphans/<id>')` | `@app.get("/orphans/{id}")` | ✅ Complete |
| Health Check | N/A | `@app.get("/health")` | ✅ New Feature |

## Key Differences

### 1. **Template Handling**
**Flask:**
```python
return render_template('summary.html', developers=dev_stats, ...)
```

**FastAPI:**
```python
return templates.TemplateResponse(
    "summary.html", 
    {"request": request, "developers": dev_stats, ...}
)
```

### 2. **Static Files**
**Flask:** Automatic static file serving
**FastAPI:** Explicit mounting required:
```python
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
```

### 3. **Error Handling**
**FastAPI** includes structured error handling with HTTP exceptions:
```python
try:
    # endpoint logic
except Exception as e:
    logger.error(f"Error: {e}")
    raise HTTPException(status_code=500, detail="Internal server error")
```

### 4. **Type Hints**
FastAPI leverages Python type hints for automatic API documentation:
```python
async def summary(request: Request, id: Optional[str] = None):
```

## Running the FastAPI Version

### Option 1: Direct execution
```bash
cd /path/to/kospex
python src/kweb2.py
```

### Option 2: Using the runner script
```bash
python run_fastapi.py
```

### Option 3: Uvicorn directly
```bash
uvicorn src.kweb2:app --host 0.0.0.0 --port 8000 --reload
```

## Features Added

1. **Automatic API Documentation**: Available at `http://localhost:8000/docs`
2. **Health Check Endpoint**: `GET /health`
3. **CORS Support**: Configured for development
4. **Structured Logging**: Better error tracking and debugging
5. **Path-based Static/Template Discovery**: More robust file handling
6. **Custom 404 Error Handling**: Serves `/src/templates/404.html` for not found errors

### 404 Error Handling

The FastAPI version includes comprehensive 404 handling:

#### Custom Exception Handler
```python
@app.exception_handler(404)
async def custom_404_handler(request: Request, exc: HTTPException):
    """Custom 404 error handler that serves the 404.html template"""
```
- Serves the `/src/templates/404.html` template for 404 errors
- Includes fallback to basic HTML if template fails to load

#### General HTTP Exception Handler  
```python
@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
```
- Routes 404 errors to the custom handler
- Uses default FastAPI handling for other HTTP errors

#### Catch-All Route
```python
@app.get("/{full_path:path}", response_class=HTMLResponse)
async def catch_all(request: Request, full_path: str):
```
- Catches any unmatched paths and raises 404
- Logs the attempted path for debugging
- **Must be the last route defined** to avoid conflicts

#### How 404 Handling Works:

1. **Explicit 404s**: When code raises `HTTPException(status_code=404)`, it uses the custom handler
2. **Unmatched Routes**: Any path not matching defined routes hits the catch-all and triggers 404
3. **Template Rendering**: Uses the same Jinja2 templates as the rest of the app
4. **Fallback Safety**: If the 404.html template fails, serves basic HTML
5. **Logging**: Logs 404 attempts for monitoring

#### Testing 404 Behavior:

Try these URLs to test the 404 handling:
- `http://localhost:8000/nonexistent-page` → Custom 404.html
- `http://localhost:8000/some/deep/path` → Custom 404.html  
- `http://localhost:8000/static/missing.css` → Will also trigger 404

The 404 page uses the same Tailwind CSS styling and navigation as the rest of the Kospex application.

## Migration Benefits

1. **Performance**: FastAPI is faster than Flask
2. **Type Safety**: Built-in type validation
3. **Auto Documentation**: Swagger/OpenAPI docs generated automatically
4. **Modern Async**: Native async/await support
5. **Better Validation**: Automatic request/response validation

## Next Steps

To complete the migration, the following Flask endpoints need to be converted:

1. All remaining routes from `kweb.py`
2. Developer detail pages
3. Repository listings
4. Organization views
5. Technology landscape
6. Dependencies and security features
7. Graph visualizations
8. File upload/download handling

## Testing

1. **Template Compatibility**: All existing Jinja2 templates work without modification
2. **Static Assets**: CSS, JS, and images served correctly
3. **Database Integration**: KospexQuery integration maintained
4. **Helper Services**: HelpService and other utilities preserved

## Configuration

The FastAPI app includes:
- **CORS Middleware**: For cross-origin requests
- **Static File Serving**: Tailwind CSS and JavaScript assets
- **Jinja2 Templates**: Same template directory as Flask version
- **Error Handling**: Structured exception handling
- **Logging**: Request and error logging

## Production Considerations

Before deploying to production:

1. **CORS Configuration**: Restrict `allow_origins` to specific domains
2. **Security Headers**: Add security middleware
3. **Rate Limiting**: Implement rate limiting for API endpoints
4. **Database Connection Pooling**: Configure async database connections
5. **Environment Configuration**: Use environment variables for settings
6. **Monitoring**: Add application performance monitoring