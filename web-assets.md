# Web Assets Management

This document describes how to manage web assets (CSS and JavaScript) for the Kospex Python package.

## Overview

Kospex uses a hybrid approach for web asset management:
- **Development**: Use Node.js/npm for dependency management and building
- **Distribution**: Include built assets in the Python package for self-contained deployment
- **SCA**: Maintain package.json/package-lock.json for Software Composition Analysis

## Directory Structure

```
kospex/
├── package.json              # JavaScript dependencies & build scripts
├── package-lock.json         # Locked versions for SCA
├── tailwind.config.js        # Tailwind CSS configuration
├── build-static.js           # Build script for copying JS dependencies
├── web-assets.md            # This documentation
├── src/
│   ├── static/
│   │   ├── css/
│   │   │   ├── input.css        # Tailwind source file
│   │   │   ├── tailwind.css     # Built CSS (checked into git)
│   │   │   └── datatables.min.css # DataTables CSS (checked into git)
│   │   └── js/
│   │       ├── d3.min.js        # Built JS files (checked into git)
│   │       ├── chart.min.js
│   │       ├── datatables.min.js
│   │       └── jquery.min.js
│   └── templates/
│       └── *.html            # HTML templates using static assets
└── pyproject.toml           # Updated to include static assets
```

## Dependencies

### CSS Framework
- **Tailwind CSS**: Utility-first CSS framework

### JavaScript Libraries
- **D3.js**: Data visualization library for bubble charts and treemaps
- **Chart.js**: Chart library for various chart types
- **DataTables**: jQuery plugin for enhanced HTML tables (includes CSS for sorting arrows and styling)
- **jQuery**: Required dependency for DataTables functionality

## Setup Instructions

### Initial Setup

1. **Install Node.js** (if not already installed):
   ```bash
   # On macOS with Homebrew
   brew install node
   
   # On Ubuntu/Debian
   sudo apt-get install nodejs npm
   ```

2. **Install dependencies**:
   ```bash
   npm install
   ```

3. **Build static assets**:
   ```bash
   npm run build
   ```

### Development Workflow

1. **Start development** (with file watching):
   ```bash
   npm run dev
   ```

2. **Make changes** to templates or CSS:
   - Edit HTML templates in `src/templates/`
   - Edit Tailwind source in `src/static/css/input.css`
   - The CSS will rebuild automatically when files change

3. **Test changes** by running your Kospex web interface

### Production Build

Before creating a release or committing changes:

1. **Build production assets**:
   ```bash
   npm run build
   ```

2. **Check for vulnerabilities**:
   ```bash
   npm audit
   ```

3. **Commit built assets**:
   ```bash
   git add src/static/ package-lock.json
   git commit -m "Update static assets and dependencies"
   ```

## Available Scripts

- `npm run build` - Build all static assets (CSS + JS)
- `npm run build-css` - Build only Tailwind CSS
- `npm run build-css-watch` - Build CSS with file watching
- `npm run copy-deps` - Copy JavaScript dependencies only
- `npm run dev` - Development mode with CSS watching
- `npm audit` - Check for security vulnerabilities

## Asset Updates

### Updating JavaScript Libraries

To update DataTables, jQuery, D3.js, Chart.js or other dependencies:

1. **Update specific library**:
   ```bash
   # Update DataTables (both JS and CSS)
   npm update datatables.net datatables.net-dt
   
   # Update jQuery
   npm update jquery
   
   # Update all dependencies
   npm update
   ```

2. **Rebuild assets**:
   ```bash
   npm run copy-deps  # Copy JS/CSS from node_modules
   npm run build-css  # Rebuild Tailwind CSS
   # Or run both:
   npm run build
   ```

3. **Test the application** to ensure compatibility
4. **Commit changes** including updated package-lock.json and static assets

**Note**: DataTables includes both JavaScript and CSS files. The build process automatically copies both the functionality (`datatables.min.js`) and styling (`datatables.min.css`) which includes sorting arrows and table styling.

### Adding New JavaScript Libraries

1. **Install the dependency**:
   ```bash
   npm install --save library-name
   ```

2. **Update build-static.js** to include the new library:
   ```javascript
   const deps = [
     // existing deps...
     {
       from: 'node_modules/library-name/dist/library.min.js',
       to: 'src/static/js/library.min.js',
       name: 'Library Name'
     },
     // For libraries with CSS:
     {
       from: 'node_modules/library-name/dist/library.min.css', 
       to: 'src/static/css/library.min.css',
       name: 'Library CSS'
     }
   ];
   ```

3. **Rebuild assets**:
   ```bash
   npm run build
   ```

4. **Update templates** to use the new library:
   ```html
   <!-- For CSS libraries -->
   <link rel="stylesheet" href="/static/css/library.min.css"/>
   <!-- For JavaScript libraries -->
   <script src="/static/js/library.min.js"></script>
   ```

### Updating Tailwind CSS

Tailwind configuration is in `tailwind.config.js`. After making changes:

1. **Rebuild CSS**:
   ```bash
   npm run build-css
   ```

2. **Test the changes** in your templates

## Software Composition Analysis (SCA)

The package.json and package-lock.json files enable:

- **Dependency tracking**: Exact versions of all JavaScript libraries
- **Vulnerability scanning**: `npm audit` for security issues
- **Automated updates**: Dependabot/Renovate support
- **License compliance**: License scanning tools can read package.json
- **SBOM generation**: Software Bill of Materials for compliance

### Running SCA Tools

```bash
# Check for vulnerabilities
npm audit

# Generate dependency tree
npm list

# Check for outdated packages
npm outdated
```

## Troubleshooting

### Node.js/npm Issues

- **Clear npm cache**: `npm cache clean --force`
- **Delete node_modules**: `rm -rf node_modules && npm install`
- **Check Node.js version**: `node --version` (requires Node.js 14+)

### Build Issues

- **Missing source files**: Ensure `src/static/css/input.css` exists
- **Permission errors**: Check file permissions on build scripts
- **Path issues**: Verify all paths in `build-static.js` are correct

### Template Issues

- **404 errors**: Ensure static assets are built and paths are correct
- **Styling issues**: Check that Tailwind CSS classes are in the built CSS
- **JavaScript errors**: Verify all required JS libraries are included

## File Distribution

The following files are included in the Python package distribution:
- `src/static/css/tailwind.css` - Built Tailwind CSS file
- `src/static/css/datatables.min.css` - DataTables CSS (sorting arrows, styling)
- `src/static/js/*.js` - All JavaScript libraries (jQuery, DataTables, D3.js, Chart.js)
- `src/templates/*.html` - HTML templates

The following files are development-only (not distributed):
- `package.json` / `package-lock.json` - Node.js dependencies
- `tailwind.config.js` - Tailwind configuration
- `build-static.js` - Build script
- `src/static/css/input.css` - Tailwind source
- `node_modules/` - Node.js dependencies folder

## DataTables Integration

DataTables requires both JavaScript and CSS files:
- **JavaScript**: `/static/js/datatables.min.js` - Table functionality
- **CSS**: `/static/css/datatables.min.css` - Sorting arrows, hover effects, pagination styling
- **Dependency**: `/static/js/jquery.min.js` - Required by DataTables

Templates include DataTables via:
```html
{% include '_footer_scripts.html' %}      <!-- Includes jQuery -->
{% include '_datatable_scripts.html' %}   <!-- Includes DataTables CSS + JS -->
```