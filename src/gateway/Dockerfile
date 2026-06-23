# ==========================================
# 第一阶段：构建微服务 (Builder)
# ==========================================
FROM maven:3.8.6-eclipse-temurin-17 AS builder
WORKDIR /build
COPY src/pom.xml .
RUN mvn dependency:go-offline -B
COPY src ./src
RUN mvn clean package -DskipTests

# ==========================================
# 第二阶段：运行微服务 (Runtime)
# ==========================================

FROM eclipse-temurin:17-jre-alpine
LABEL description="Spring Cloud Gateway"

ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

WORKDIR /app
COPY --from=builder /build/target/*.jar app.jar
EXPOSE 8080
ENV JAVA_OPTS="-Xms512m -Xmx512m -Djava.security.egd=file:/dev/./urandom"
ENTRYPOINT java $JAVA_OPTS -jar app.jar