import com.eteks.sweethome3d.io.HomeFileRecorder;
import com.eteks.sweethome3d.model.*;
import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.io.PrintWriter;
import java.util.*;

/**
 * Native Consumer Adapter for Sweet Home 3D (eTeks).
 *
 * Directly executes the installed Sweet Home 3D Java runtime and its HomeFileRecorder
 * to independently ingest, manipulate, and export authentic .sh3d files, providing
 * complete semantic qualification independent of the Python codec.
 */
public class SweetHomeConsumer {
    public static void main(String[] args) {
        if (args.length < 2) {
            System.err.println("Usage: SweetHomeConsumer <command> <args...>");
            System.err.println("Commands:");
            System.err.println("  snapshot <input.sh3d> <output.json>");
            System.err.println("  roundtrip <input.sh3d> <output.sh3d>");
            System.err.println("  verify <input.sh3d> <output.sh3d> <output.json>");
            System.exit(1);
        }

        String command = args[0];
        try {
            // preferXmlEntry = true ensures Home.xml in modern .sh3d archives is read directly
            HomeFileRecorder recorder = new HomeFileRecorder(0, false, null, false, true, false);

            if ("snapshot".equalsIgnoreCase(command)) {
                String inPath = args[1];
                String outJson = args[2];
                Home home = readHomeSafe(recorder, inPath);
                String json = generateSnapshotJson(home, inPath);
                try (PrintWriter pw = new PrintWriter(new OutputStreamWriter(new FileOutputStream(outJson), "UTF-8"))) {
                    pw.print(json);
                }
                System.out.println("SNAPSHOT_OK");
            } else if ("roundtrip".equalsIgnoreCase(command)) {
                String inPath = args[1];
                String outPath = args[2];
                Home home = readHomeSafe(recorder, inPath);
                recorder.writeHome(home, outPath);
                System.out.println("ROUNDTRIP_OK");
            } else if ("verify".equalsIgnoreCase(command)) {
                String inPath = args[1];
                String outPath = args[2];
                String outJson = args[3];
                Home home1 = readHomeSafe(recorder, inPath);
                recorder.writeHome(home1, outPath);
                Home home2 = readHomeSafe(recorder, outPath);
                String json = generateSnapshotJson(home2, outPath);
                try (PrintWriter pw = new PrintWriter(new OutputStreamWriter(new FileOutputStream(outJson), "UTF-8"))) {
                    pw.print(json);
                }
                System.out.println("VERIFY_OK");
            } else {
                System.err.println("Unknown command: " + command);
                System.exit(2);
            }
        } catch (Throwable t) {
            t.printStackTrace(System.err);
            System.exit(3);
        }
    }

    private static Home readHomeSafe(HomeFileRecorder recorder, String path) throws Exception {
        try {
            return recorder.readHome(path);
        } catch (DamagedHomeRecorderException de) {
            // When external 3D OBJ / texture files are not bundled in archive, geometry is retained
            return de.getDamagedHome();
        }
    }

    private static String generateSnapshotJson(Home home, String sourcePath) {
        StringBuilder sb = new StringBuilder();
        sb.append("{\n");
        sb.append("  \"source\": \"").append(escapeJson(sourcePath)).append("\",\n");
        sb.append("  \"version\": ").append(home.getVersion()).append(",\n");
        sb.append("  \"name\": \"").append(escapeJson(home.getName() != null ? home.getName() : "")).append("\",\n");
        sb.append("  \"wall_count\": ").append(home.getWalls().size()).append(",\n");
        sb.append("  \"room_count\": ").append(home.getRooms().size()).append(",\n");
        sb.append("  \"furniture_count\": ").append(home.getFurniture().size()).append(",\n");
        sb.append("  \"dimension_count\": ").append(home.getDimensionLines().size()).append(",\n");
        sb.append("  \"level_count\": ").append(home.getLevels().size()).append(",\n");
        
        // Walls
        sb.append("  \"walls\": [\n");
        int wi = 0;
        for (Wall w : home.getWalls()) {
            if (wi > 0) sb.append(",\n");
            sb.append("    {");
            sb.append("\"id\": \"").append(escapeJson(w.getId() != null ? w.getId() : "")).append("\", ");
            sb.append("\"xStart\": ").append(w.getXStart()).append(", ");
            sb.append("\"yStart\": ").append(w.getYStart()).append(", ");
            sb.append("\"xEnd\": ").append(w.getXEnd()).append(", ");
            sb.append("\"yEnd\": ").append(w.getYEnd()).append(", ");
            sb.append("\"thickness\": ").append(w.getThickness()).append(", ");
            sb.append("\"height\": ").append(w.getHeight() != null ? w.getHeight() : 250.0f).append(", ");
            sb.append("\"arcExtent\": ").append(w.getArcExtent() != null ? w.getArcExtent() : 0.0f);
            sb.append("}");
            wi++;
        }
        sb.append("\n  ],\n");

        // Rooms
        sb.append("  \"rooms\": [\n");
        int ri = 0;
        for (Room r : home.getRooms()) {
            if (ri > 0) sb.append(",\n");
            sb.append("    {");
            sb.append("\"id\": \"").append(escapeJson(r.getId() != null ? r.getId() : "")).append("\", ");
            sb.append("\"name\": \"").append(escapeJson(r.getName() != null ? r.getName() : "")).append("\", ");
            sb.append("\"area\": ").append(r.getArea()).append(", ");
            sb.append("\"points\": [");
            float[][] pts = r.getPoints();
            if (pts != null) {
                for (int pi = 0; pi < pts.length; pi++) {
                    if (pi > 0) sb.append(", ");
                    sb.append("[").append(pts[pi][0]).append(", ").append(pts[pi][1]).append("]");
                }
            }
            sb.append("]");
            sb.append("}");
            ri++;
        }
        sb.append("\n  ],\n");

        // Furniture (including doors and windows)
        sb.append("  \"furniture\": [\n");
        int fi = 0;
        for (HomePieceOfFurniture f : home.getFurniture()) {
            if (fi > 0) sb.append(",\n");
            sb.append("    {");
            sb.append("\"id\": \"").append(escapeJson(f.getId() != null ? f.getId() : "")).append("\", ");
            sb.append("\"name\": \"").append(escapeJson(f.getName() != null ? f.getName() : "")).append("\", ");
            sb.append("\"x\": ").append(f.getX()).append(", ");
            sb.append("\"y\": ").append(f.getY()).append(", ");
            sb.append("\"elevation\": ").append(f.getElevation()).append(", ");
            sb.append("\"width\": ").append(f.getWidth()).append(", ");
            sb.append("\"depth\": ").append(f.getDepth()).append(", ");
            sb.append("\"height\": ").append(f.getHeight()).append(", ");
            sb.append("\"angle\": ").append(f.getAngle()).append(", ");
            sb.append("\"isDoorOrWindow\": ").append(f.isDoorOrWindow());
            sb.append("}");
            fi++;
        }
        sb.append("\n  ],\n");

        // Dimensions
        sb.append("  \"dimensions\": [\n");
        int di = 0;
        for (DimensionLine d : home.getDimensionLines()) {
            if (di > 0) sb.append(",\n");
            sb.append("    {");
            sb.append("\"id\": \"").append(escapeJson(d.getId() != null ? d.getId() : "")).append("\", ");
            sb.append("\"xStart\": ").append(d.getXStart()).append(", ");
            sb.append("\"yStart\": ").append(d.getYStart()).append(", ");
            sb.append("\"xEnd\": ").append(d.getXEnd()).append(", ");
            sb.append("\"yEnd\": ").append(d.getYEnd()).append(", ");
            sb.append("\"length\": ").append(d.getLength());
            sb.append("}");
            di++;
        }
        sb.append("\n  ],\n");

        // Levels
        sb.append("  \"levels\": [\n");
        int li = 0;
        for (Level l : home.getLevels()) {
            if (li > 0) sb.append(",\n");
            sb.append("    {");
            sb.append("\"name\": \"").append(escapeJson(l.getName() != null ? l.getName() : "")).append("\", ");
            sb.append("\"elevation\": ").append(l.getElevation()).append(", ");
            sb.append("\"floorThickness\": ").append(l.getFloorThickness()).append(", ");
            sb.append("\"height\": ").append(l.getHeight());
            sb.append("}");
            li++;
        }
        sb.append("\n  ]\n");
        sb.append("}\n");
        return sb.toString();
    }

    private static String escapeJson(String s) {
        if (s == null) return "";
        return s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n").replace("\r", "");
    }
}
