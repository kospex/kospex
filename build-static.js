const fs = require('fs');
const path = require('path');

console.log('Building static JavaScript assets...');

// Define the dependencies to copy
const deps = [
  {
    from: 'node_modules/d3/dist/d3.min.js',
    to: 'src/static/js/d3.min.js',
    name: 'D3.js'
  },
  {
    from: 'node_modules/chart.js/dist/chart.umd.min.js', 
    to: 'src/static/js/chart.min.js',
    name: 'Chart.js'
  },
  {
    from: 'node_modules/datatables.net/js/jquery.dataTables.min.js',
    to: 'src/static/js/datatables.min.js',
    name: 'DataTables'
  },
  {
    from: 'node_modules/jquery/dist/jquery.min.js',
    to: 'src/static/js/jquery.min.js',
    name: 'jQuery'
  },
  {
    from: 'node_modules/datatables.net-dt/css/jquery.dataTables.min.css',
    to: 'src/static/css/datatables.min.css',
    name: 'DataTables CSS'
  }
];

// Ensure directories exist
const staticJsDir = 'src/static/js';
const staticCssDir = 'src/static/css';

console.log(`Creating directories...`);
if (!fs.existsSync('src')) {
  fs.mkdirSync('src');
}
if (!fs.existsSync('src/static')) {
  fs.mkdirSync('src/static');
}
if (!fs.existsSync(staticJsDir)) {
  fs.mkdirSync(staticJsDir, { recursive: true });
  console.log(`  ✓ Created ${staticJsDir}`);
}
if (!fs.existsSync(staticCssDir)) {
  fs.mkdirSync(staticCssDir, { recursive: true });
  console.log(`  ✓ Created ${staticCssDir}`);
}

// Copy JavaScript files
console.log('\nCopying JavaScript dependencies...');
let successCount = 0;
let errorCount = 0;

deps.forEach(dep => {
  try {
    if (fs.existsSync(dep.from)) {
      fs.copyFileSync(dep.from, dep.to);
      const stats = fs.statSync(dep.to);
      const sizeKB = Math.round(stats.size / 1024);
      console.log(`  ✓ ${dep.name}: ${dep.from} -> ${dep.to} (${sizeKB} KB)`);
      successCount++;
    } else {
      console.error(`  ✗ ${dep.name}: Source file not found: ${dep.from}`);
      errorCount++;
    }
  } catch (error) {
    console.error(`  ✗ ${dep.name}: Error copying file: ${error.message}`);
    errorCount++;
  }
});

// Summary
console.log(`\n📊 Summary:`);
console.log(`  • Successfully copied: ${successCount} files`);
if (errorCount > 0) {
  console.log(`  • Errors: ${errorCount} files`);
  process.exit(1);
} else {
  console.log(`  • No errors`);
}

console.log(`\n✅ Static JavaScript assets build completed!`);
console.log(`\nNext steps:`);
console.log(`  1. Run 'npm run build-css' to build Tailwind CSS`);
console.log(`  2. Update your HTML templates to use local assets`);
console.log(`  3. Test your application`);