FROM nginx:1.27-alpine

# Remove default Nginx config
RUN rm /etc/nginx/conf.d/default.conf

# Replace symlinks (access.log -> /dev/stdout, error.log -> /dev/stderr)
# with real files so the SIEM Ingestor can tail them.
# Tee approach (writing to both stdout AND a file) is more complex;
# we just keep file-based logging here.
RUN rm -f /var/log/nginx/access.log /var/log/nginx/error.log \
    && touch /var/log/nginx/access.log /var/log/nginx/error.log \
    && chown -R nginx:nginx /var/log/nginx

# Copy our custom config
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Expose HTTP port
EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]