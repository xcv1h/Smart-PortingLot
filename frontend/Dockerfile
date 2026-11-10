# The frontend is only static files, so the image is plain nginx with our
# config and three files added. "stable" is the nginx release line that only
# gets bug fixes, and "alpine" keeps the image small.
FROM nginx:stable-alpine

# The base image already loads every file in /etc/nginx/conf.d/ from inside
# its http { } block. Overwriting default.conf replaces the stock "welcome"
# site with ours, without having to rewrite the whole nginx configuration.
COPY nginx.conf /etc/nginx/conf.d/default.conf

# /usr/share/nginx/html is the directory nginx.conf serves files from.
COPY index.html style.css app.js /usr/share/nginx/html/

# No EXPOSE or CMD here on purpose: the base image already declares port 80
# and starts nginx in the foreground, which is what a container needs.
