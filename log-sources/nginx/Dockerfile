FROM nginx:1.27-alpine

# Remove default Nginx config
RUN rm /etc/nginx/conf.d/default.conf

# Disable the default access_log directive in main nginx.conf
# so only our siem_combined directive in conf.d/default.conf logs requests
RUN sed -i 's|access_log\s*/var/log/nginx/access.log\s*main;|# &|' /etc/nginx/nginx.conf

# Replace symlinks (access.log -> /dev/stdout, error.log -> /dev/stderr)
# with real files so the SIEM Ingestor can tail them.
RUN rm -f /var/log/nginx/access.log /var/log/nginx/error.log \
    && touch /var/log/nginx/access.log /var/log/nginx/error.log \
    && chown -R nginx:nginx /var/log/nginx

# Copy our custom config
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]